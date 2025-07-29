import re
import unicodedata
import random
import string

def slugify(text):
    """
    Converts a string to a URL-friendly slug.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)

def generate_short_code(length=6):
    """
    Generates a random alphanumeric short code of given length.
    Used for creating temporary short registration URLs.

    :param length: Length of the short code (default: 6)
    :return: A string like 'a7b9K2'
    """
    characters = string.ascii_letters + string.digits
    return ''.join(random.choices(characters, k=length))
