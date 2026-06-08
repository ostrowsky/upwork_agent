"""Live batch submit for an explicit, ordered list of job ids.

Unlike submit_ready (oldest-first, whole task), this submits EXACTLY the job ids
you pass, in the order given — so you control which fresh, case-attached jobs spend
your connects. Stops early once connects are exhausted.

Usage:
  python submit_batch.py 83 82 81 77 73 68 63        # LIVE if AUTO_SUBMIT=1
  python submit_batch.py 83 --dry-run                # never clicks Send

Each job opens the apply form (self-heal handles a wedged Edge profile), fills the
cover letter + rate, attaches the generated case PDF, and submits.
"""
import sys

from database import get_db_session, Job, Proposal
from submit import submit_proposal, auto_submit_enabled


def main() -> int:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    ids = [int(x) for x in args if x.isdigit()]
    if not ids:
        print("usage: python submit_batch.py <job_id> [job_id ...] [--dry-run]")
        return 1

    live = auto_submit_enabled() and not dry
    print(f"[batch] mode={'LIVE (real submit!)' if live else 'DRY-RUN'} jobs={ids}", flush=True)

    db = get_db_session()
    sent = errors = insufficient = 0
    try:
        for jid in ids:
            job = db.query(Job).filter(Job.id == jid).first()
            if job is None:
                print(f"#{jid}: not found, skip", flush=True)
                continue
            if job.status == "SENT":
                print(f"#{jid}: already SENT, skip", flush=True)
                continue
            proposal = (
                db.query(Proposal)
                .filter(Proposal.job_id == jid, Proposal.status == "DRAFT")
                .first()
            )
            print(f"#{jid} '{(job.title or '')[:45]}' → submitting…", flush=True)
            res = submit_proposal(job, proposal, db, dry_run=(True if dry else None))
            reason = (res.get("reason") or "")
            print(f"   result: submitted={res.get('submitted')} connects={res.get('connects')} "
                  f"attached={res.get('attached')}/{res.get('attached_verified')} | {reason[:90]}", flush=True)
            if res.get("submitted"):
                sent += 1
            elif "insufficient connects" in reason.lower():
                insufficient += 1
                if insufficient >= 2:
                    print("[batch] connects exhausted — stopping.", flush=True)
                    break
            elif not res.get("dry_run"):
                errors += 1
        print(f"[batch] done: sent={sent}, errors={errors}, insufficient_stops={insufficient}", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
