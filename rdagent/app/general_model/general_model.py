import fire
from rdagent.components.coder.model_coder.task_loader import (
    ModelExperimentLoaderFromPDFfiles,
)
from rdagent.components.document_reader.document_reader import (
    extract_first_page_screenshot_from_pdf,
)
from rdagent.log import rdagent_logger as logger
from rdagent.log.conf import LOG_SETTINGS
from rdagent.scenarios.general_model.scenario import GeneralModelScenario
from rdagent.scenarios.qlib.developer.model_coder import QlibModelCoSTEER
from rdagent.utils.langfuse_tracing import (
    agent_run,
    captures_content,
    flush,
    new_run_id,
    observe,
    score_failure,
    session_id_from_log_path,
    update_observation,
)


def extract_models_and_implement(report_file_path: str) -> None:
    """
    This is a research copilot to automatically implement models from a report file or paper.

    It extracts models from a given PDF report file and implements the necessary operations.

    Parameters:
    report_file_path (str): The path to the report file. The file must be a PDF file.

    Example URLs of PDF reports:
    - https://arxiv.org/pdf/2210.09789
    - https://arxiv.org/pdf/2305.10498
    - https://arxiv.org/pdf/2110.14446
    - https://arxiv.org/pdf/2205.12454
    - https://arxiv.org/pdf/2210.16518

    Returns:
    None
    """
    run_id = new_run_id()
    traced_report = report_file_path if captures_content() else None
    try:
        with agent_run(
            "run-general-model",
            input_data={"run_id": run_id, "report": traced_report},
            session_id=session_id_from_log_path(LOG_SETTINGS.trace_path),
            tags=["rdagent", "general-model", "agent-chain"],
            metadata={"scenario": "general-model", "run_id": run_id},
        ) as run_observation:
            try:
                scenario = GeneralModelScenario()
                logger.log_object(scenario, tag="scenario")

                with observe(
                    "extract-document-preview",
                    as_type="retriever",
                    input_data={"report": traced_report},
                ) as preview_observation:
                    img = extract_first_page_screenshot_from_pdf(report_file_path)
                    logger.log_object(img, tag="pdf_image")
                    update_observation(preview_observation, output={"status": "extracted"})

                with observe(
                    "extract-model-specification",
                    as_type="agent",
                    input_data={"report": traced_report},
                ) as extraction_observation:
                    exp = ModelExperimentLoaderFromPDFfiles().load(report_file_path)
                    logger.log_object(exp, tag="load_experiment")
                    update_observation(
                        extraction_observation,
                        output={"experiment_type": type(exp).__name__, "subtask_count": len(exp.sub_tasks)},
                    )

                with observe(
                    "implement-paper-model",
                    as_type="agent",
                    input_data={"experiment_type": type(exp).__name__, "subtask_count": len(exp.sub_tasks)},
                ) as implementation_observation:
                    exp = QlibModelCoSTEER(scenario).develop(exp)
                    logger.log_object(exp, tag="developed_experiment")
                    update_observation(
                        implementation_observation,
                        output={
                            "status": "completed",
                            "workspace_count": len(exp.sub_workspace_list),
                        },
                    )
                update_observation(
                    run_observation,
                    output={
                        "status": "completed",
                        "run_id": run_id,
                        "implemented_models": len(exp.sub_workspace_list),
                    },
                )
            except BaseException as error:
                update_observation(
                    run_observation,
                    output={"status": "failed", "run_id": run_id, "error_type": type(error).__name__},
                    level="ERROR",
                    status_message=f"{type(error).__name__}: {error}",
                )
                score_failure(run_observation, error)
                raise
    finally:
        flush()


if __name__ == "__main__":
    fire.Fire(extract_models_and_implement)
