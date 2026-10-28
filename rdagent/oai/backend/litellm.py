import copyreg
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict, cast

import numpy as np
from litellm import (
    completion,
    completion_cost,
    embedding,
    get_model_info,
    supports_response_schema,
    token_counter,
)
from litellm.exceptions import BadRequestError, Timeout
from pydantic import BaseModel
from rdagent.log import LogColors
from rdagent.log import rdagent_logger as logger
from rdagent.oai.backend.base import APIBackend
from rdagent.oai.llm_conf import LLMSettings
from rdagent.utils.langfuse_tracing import (
    captures_content,
    configure_litellm,
    observe,
    prompt_link_for_messages,
    redact_secrets,
    update_observation,
)


# NOTE: Patching! Otherwise, the exception will call the constructor and with following error:
# `BadRequestError.__init__() missing 2 required positional arguments: 'model' and 'llm_provider'`
def _reduce_no_init(exc: Exception) -> tuple:
    cls = exc.__class__
    return (cls.__new__, (cls,), exc.__dict__)


# suppose you want to apply this to MyError
for cls in [BadRequestError, Timeout]:
    copyreg.pickle(cls, _reduce_no_init)


class LiteLLMSettings(LLMSettings):
    class Config:
        env_prefix = "LITELLM_"
        """Use `LITELLM_` as prefix for environment variables"""

    # Placeholder for LiteLLM specific settings, so far it's empty


LITELLM_SETTINGS = LiteLLMSettings()
ACC_COST = 0.0


def _usage_value(usage: Any, key: str) -> Any:
    """Read a LiteLLM usage field from either its model or dict form."""

    if usage is None:
        return None
    value = getattr(usage, key, None)
    if value is not None:
        return value
    if isinstance(usage, dict):
        return usage.get(key)
    return None


class LiteLLMAPIBackend(APIBackend):
    """LiteLLM implementation of APIBackend interface"""

    _has_logged_settings: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        configure_litellm()
        if not self.__class__._has_logged_settings:
            safe_settings = redact_secrets(LITELLM_SETTINGS.model_dump())
            logger.info(f"{safe_settings}")
            logger.log_object(safe_settings, tag="LITELLM_SETTINGS")
            self.__class__._has_logged_settings = True
        super().__init__(*args, **kwargs)

    def _calculate_token_from_messages(self, messages: list[dict[str, Any]]) -> int:
        """
        Calculate the token count from messages
        """
        num_tokens = token_counter(
            model=LITELLM_SETTINGS.chat_model,
            messages=messages,
        )
        logger.info(f"{LogColors.CYAN}Token count: {LogColors.END} {num_tokens}", tag="debug_litellm_token")
        return num_tokens

    def _create_embedding_inner_function(self, input_content_list: list[str]) -> list[list[float]]:
        """
        Call the embedding function
        """
        model_name = LITELLM_SETTINGS.embedding_model
        logger.info(f"{LogColors.GREEN}Using emb model{LogColors.END} {model_name}", tag="debug_litellm_emb")
        if LITELLM_SETTINGS.log_llm_chat_content:
            logger.info(
                f"{LogColors.MAGENTA}Creating embedding{LogColors.END} for: {input_content_list}",
                tag="debug_litellm_emb",
            )
        traced_input: Any = input_content_list if captures_content() else {"item_count": len(input_content_list)}
        with observe(
            "create-embedding",
            as_type="embedding",
            input_data=traced_input,
            model=model_name,
        ) as observation:
            response = embedding(model=model_name, input=input_content_list)
            response_list = [data["embedding"] for data in response.data]
            usage = getattr(response, "usage", None)
            input_tokens = int(_usage_value(usage, "prompt_tokens") or 0)
            total_tokens = int(_usage_value(usage, "total_tokens") or input_tokens)
            update_observation(
                observation,
                output={
                    "embedding_count": len(response_list),
                    "dimensions": len(response_list[0]) if response_list else 0,
                },
                model=model_name,
                usage_details={"input": input_tokens, "total": total_tokens},
            )
        return response_list

    class CompleteKwargs(TypedDict):
        model: str
        temperature: float
        max_tokens: int | None
        reasoning_effort: Literal["low", "medium", "high"] | None

    def get_complete_kwargs(self) -> CompleteKwargs:
        """
        return several key settings for completion
        getting these values from settings makes it easier to adapt to backend calls in agent systems.
        """
        # Call LiteLLM completion
        model = LITELLM_SETTINGS.chat_model
        temperature = LITELLM_SETTINGS.chat_temperature
        max_tokens = LITELLM_SETTINGS.chat_max_tokens
        reasoning_effort = LITELLM_SETTINGS.reasoning_effort

        if LITELLM_SETTINGS.chat_model_map:
            for t, mc in LITELLM_SETTINGS.chat_model_map.items():
                if t in logger._tag:
                    model = mc["model"]
                    if "temperature" in mc:
                        temperature = float(mc["temperature"])
                    if "max_tokens" in mc:
                        max_tokens = int(mc["max_tokens"])
                    if "reasoning_effort" in mc:
                        if mc["reasoning_effort"] in ["low", "medium", "high"]:
                            reasoning_effort = cast("Literal['low', 'medium', 'high']", mc["reasoning_effort"])
                        else:
                            reasoning_effort = None
                    break
        return self.CompleteKwargs(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

    def _create_chat_completion_inner_function(  # type: ignore[no-untyped-def] # noqa: C901, PLR0912, PLR0915
        self,
        messages: list[dict[str, Any]],
        response_format: dict | type[BaseModel] | None = None,
        *args,
        **kwargs,
    ) -> tuple[str, str | None]:
        """
        Call the chat completion function
        """

        if response_format and not supports_response_schema(model=LITELLM_SETTINGS.chat_model):
            # Deepseek will enter this branch
            logger.warning(
                f"{LogColors.YELLOW}Model {LITELLM_SETTINGS.chat_model} does not support response schema, ignoring response_format argument.{LogColors.END}",
                tag="llm_messages",
            )
            response_format = None

        if response_format:
            kwargs["response_format"] = response_format

        if LITELLM_SETTINGS.log_llm_chat_content:
            logger.info(self._build_log_messages(messages), tag="llm_messages")

        complete_kwargs = self.get_complete_kwargs()
        model = complete_kwargs["model"]
        traced_input: Any = messages if captures_content() else {"message_count": len(messages)}
        model_parameters = {
            key: value for key, value in complete_kwargs.items() if key != "model" and value is not None
        }
        if LITELLM_SETTINGS.chat_stream:
            stream_options = dict(kwargs.get("stream_options") or {})
            stream_options.setdefault("include_usage", True)
            kwargs["stream_options"] = stream_options
        linked_prompt, prompt_templates = prompt_link_for_messages(messages)
        with observe(
            "generate-chat-response",
            as_type="generation",
            input_data=traced_input,
            metadata={"prompt_templates": prompt_templates} if prompt_templates else None,
            model=model,
            model_parameters=model_parameters,
            prompt=linked_prompt,
        ) as observation:
            response = completion(
                messages=messages,
                stream=LITELLM_SETTINGS.chat_stream,
                max_retries=0,
                **complete_kwargs,
                **kwargs,
            )
            if LITELLM_SETTINGS.log_llm_chat_content:
                logger.info(f"{LogColors.GREEN}Using chat model{LogColors.END} {model}", tag="llm_messages")

            if LITELLM_SETTINGS.chat_stream:
                if LITELLM_SETTINGS.log_llm_chat_content:
                    logger.info(f"{LogColors.BLUE}assistant:{LogColors.END}", tag="llm_messages")
                content = ""
                finish_reason = None
                response_usage = None
                completion_start_time = None
                for message in response:
                    if getattr(message, "usage", None) is not None:
                        response_usage = message.usage
                    choices = message.get("choices") or []
                    if not choices:
                        continue
                    if choices[0]["finish_reason"]:
                        finish_reason = choices[0]["finish_reason"]
                    if "content" in choices[0]["delta"]:
                        chunk = choices[0]["delta"]["content"] or ""  # finish_reason chunks contain None
                        content += chunk
                        if chunk and completion_start_time is None:
                            completion_start_time = datetime.now(timezone.utc)
                        if LITELLM_SETTINGS.log_llm_chat_content:
                            logger.info(LogColors.CYAN + chunk + LogColors.END, raw=True, tag="llm_messages")
                if LITELLM_SETTINGS.log_llm_chat_content:
                    logger.info("\n", raw=True, tag="llm_messages")
            else:
                content = str(response.choices[0].message.content)
                finish_reason = response.choices[0].finish_reason
                response_usage = getattr(response, "usage", None)
                completion_start_time = None
                finish_reason_str = (
                    f"({LogColors.RED}Finish reason: {finish_reason}{LogColors.END})"
                    if finish_reason and finish_reason != "stop"
                    else ""
                )
                if LITELLM_SETTINGS.log_llm_chat_content:
                    logger.info(
                        f"{LogColors.BLUE}assistant:{LogColors.END} {finish_reason_str}\n{content}",
                        tag="llm_messages",
                    )

            global ACC_COST
            provider_cost = _usage_value(response_usage, "cost")
            if provider_cost is not None:
                cost = float(provider_cost)
            else:
                try:
                    cost = completion_cost(model=model, messages=messages, completion=content)
                except Exception as e:
                    logger.warning(f"Cost calculation failed for model {model}: {e}. Skip cost statistics.")
                    cost = np.nan
            if np.isfinite(cost):
                ACC_COST += cost
                if LITELLM_SETTINGS.log_llm_chat_content:
                    logger.info(
                        f"Current Cost: ${float(cost):.10f}; Accumulated Cost: ${float(ACC_COST):.10f}; "
                        f"{finish_reason=}",
                    )

            provider_prompt_tokens = _usage_value(response_usage, "prompt_tokens")
            provider_completion_tokens = _usage_value(response_usage, "completion_tokens")
            if provider_prompt_tokens is not None and provider_completion_tokens is not None:
                prompt_tokens = int(provider_prompt_tokens)
                completion_tokens = int(provider_completion_tokens)
            else:
                try:
                    prompt_tokens = token_counter(model=model, messages=messages)
                    completion_tokens = token_counter(model=model, text=content)
                except ValueError as e:
                    logger.warning(f"Token counting failed for model {model}: {e}. Skip token statistics.")
                    prompt_tokens = 0
                    completion_tokens = 0
            traced_output: Any = (
                {"content": content, "finish_reason": finish_reason}
                if captures_content()
                else {"finish_reason": finish_reason}
            )
            cost_details = {"total": float(cost)} if np.isfinite(cost) else None
            update_observation(
                observation,
                output=traced_output,
                model=model,
                usage_details={
                    "input": prompt_tokens,
                    "output": completion_tokens,
                    "total": prompt_tokens + completion_tokens,
                },
                cost_details=cost_details,
                completion_start_time=completion_start_time,
            )
        logger.log_object(
            {
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
                "accumulated_cost": ACC_COST,
            },
            tag="token_cost",
        )
        return content, finish_reason

    def supports_response_schema(self) -> bool:
        """
        Check if the backend supports function calling
        """
        return supports_response_schema(model=LITELLM_SETTINGS.chat_model) and LITELLM_SETTINGS.enable_response_schema

    @property
    def chat_token_limit(self) -> int:
        """Suggest an input token limit, ensuring enough space in the context window for the maximum output tokens."""
        try:
            model_info = get_model_info(LITELLM_SETTINGS.chat_model)
            if model_info is None:
                return super().chat_token_limit

            max_input = model_info.get("max_input_tokens")
            max_output = model_info.get("max_output_tokens")

            if max_input is None or max_output is None:
                return super().chat_token_limit

            max_input_tokens = max_input - max_output
            return max_input_tokens
        except Exception:
            return super().chat_token_limit
