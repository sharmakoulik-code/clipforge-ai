import argparse

from app.core.database import SessionLocal
from app.models.models import AuditLog, User


def main() -> None:
    parser = argparse.ArgumentParser(description="Administrative maintenance commands (run only from a trusted shell).")
    sub = parser.add_subparsers(dest="command", required=True)
    promote = sub.add_parser("promote-admin", help="Grant administrator role to an existing account.")
    promote.add_argument("email")
    demote = sub.add_parser("demote-admin", help="Remove administrator role from an account.")
    demote.add_argument("email")
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == args.email.strip().lower()).first()
        if not user:
            parser.error("No account exists for that email; register it first.")
        user.is_admin = args.command == "promote-admin"
        db.add(AuditLog(actor_user_id=None, action=f"cli.{args.command}", target_type="user", target_id=user.id,
                        details={"email": user.email}))
        db.commit()
        print(f"Administrator role {'granted to' if user.is_admin else 'removed from'} {user.email}.")


if __name__ == "__main__":
    main()
