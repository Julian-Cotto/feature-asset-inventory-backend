from .config import JobSettings
from .runner import run_job

def test_run_job_executes_without_error() -> None:
    run_job(JobSettings(feature_key="asset-inventory", job_name="stale-repair-check", schedule="0 3 * * *"))