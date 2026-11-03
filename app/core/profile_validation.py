import re
import unicodedata


def normalise_full_name(value: str | None) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("Full name must be text.")

    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        for character in value
    ):
        raise ValueError(
            "Full name contains unsupported characters."
        )

    name = " ".join(value.split())

    if (
        not 2 <= len(name) <= 100
        or not any(character.isalpha() for character in name)
    ):
        raise ValueError(
            "Full name must contain letters and be "
            "between 2 and 100 characters."
        )

    return name


def normalise_phone_number(value: str | None) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError("Phone number must be text.")

    if len(value) > 64 or any(
        unicodedata.category(character) in {"Cc", "Cs"}
        for character in value
    ):
        raise ValueError(
            "Enter a valid international phone number."
        )

    phone = re.sub(r"[\s().-]", "", value)

    if not phone:
        return None

    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise ValueError(
            "Use an international phone number "
            "with + and 8 to 15 digits."
        )

    return phone