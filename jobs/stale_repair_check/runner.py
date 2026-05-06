from .config import JobSettings
from app.runtime.database import db_session



def run_job(settings: JobSettings) -> None:
    print(f"[scheduled-job] feature={settings.feature_key} job={settings.job_name} schedule={settings.schedule}")
    with db_session() as db:
        _ = db


