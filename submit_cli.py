"""Manual submit helper — keeps the browser open so you can inspect the result.

Usage:
  python submit_cli.py <job_id>              # submit (LIVE if AUTO_SUBMIT=1), browser stays open
  python submit_cli.py <job_id> --dry-run    # force dry-run (never clicks Send)
  python submit_cli.py <job_id> --revert      # reset that job's proposal back to DRAFT
  python submit_cli.py <job_id> --no-keep     # close browser immediately after

By default the browser is held open until you press Enter (SUBMIT_KEEP_OPEN=1).
"""
import os
import sys


def main() -> int:
    args = sys.argv[1:]
    if not args or not args[0].isdigit():
        print("usage: python submit_cli.py <job_id> [--dry-run] [--revert] [--no-keep]")
        return 1
    job_id = int(args[0])

    if "--no-keep" not in args:
        os.environ["SUBMIT_KEEP_OPEN"] = "1"

    from database import get_db_session, Job, Proposal

    db = get_db_session()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        proposal = (
            db.query(Proposal)
            .filter(Proposal.job_id == job_id)
            .order_by(Proposal.id.desc())
            .first()
        )
        if job is None:
            print(f"job #{job_id} not found")
            return 1

        if "--revert" in args:
            if proposal:
                proposal.status = "DRAFT"
                proposal.submitted_at = None
                proposal.connects_spent = None
            job.status = "PROPOSAL_DRAFTED"
            db.commit()
            print(f"reverted job #{job_id} → PROPOSAL_DRAFTED / proposal DRAFT")
            return 0

        from submit import submit_proposal, auto_submit_enabled

        dry = "--dry-run" in args or None
        mode = "DRY-RUN" if (dry or not auto_submit_enabled()) else "LIVE (real send!)"
        print(f"[submit_cli] job #{job_id} '{(job.title or '')[:50]}' | mode={mode}")
        res = submit_proposal(job, proposal, db, dry_run=(True if dry else None))
        print(res)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
