from .config import get_job_settings
from .runner import run_job

def main() -> int:
    settings = get_job_settings()
    run_job(settings)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())