class AccountAccessRestricted(Exception):
    def __init__(self, *, blocked: bool):
        self.code = "account_blocked" if blocked else "account_deactivated"
        message = (
            "Your account has been blocked. Contact an administrator."
            if blocked else
            "Your account has been deactivated. Contact an administrator."
        )
        super().__init__(message)


def account_can_sign_in(user) -> bool:
    """Older test doubles default to the pre-status account behaviour."""
    return (
        getattr(user, "is_active", True) is True
        and getattr(user, "is_blocked", False) is False
    )
