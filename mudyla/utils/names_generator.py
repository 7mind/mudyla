"""Deterministic name generator for context identifiers.

Generates human-readable names from hash values using adjective-surname combinations.
Inspired by Docker's container naming scheme.
"""

from functools import lru_cache


ADJECTIVES: tuple[str, ...] = (
    "admiring", "adoring", "affectionate", "agitated", "amazing",
    "angry", "awesome", "blissful", "boring", "brave",
    "clever", "cocky", "compassionate", "competent", "condescending",
    "confident", "cranky", "dazzling", "determined", "distracted",
    "dreamy", "eager", "ecstatic", "elastic", "elated",
    "elegant", "eloquent", "epic", "fervent", "festive",
    "flamboyant", "focused", "friendly", "frosty", "gallant",
    "gifted", "goofy", "gracious", "happy", "hardcore",
    "heuristic", "hopeful", "hungry", "infallible", "inspiring",
    "jolly", "jovial", "keen", "kind", "laughing",
    "loving", "lucid", "mystifying", "modest", "musing",
    "naughty", "nervous", "nifty", "nostalgic", "objective",
    "optimistic", "peaceful", "pedantic", "pensive", "practical",
    "priceless", "quirky", "quizzical", "recursing", "relaxed",
    "reverent", "romantic", "sad", "serene", "sharp",
    "silly", "sleepy", "stoic", "stupefied", "suspicious",
    "tender", "thirsty", "trusting", "unruffled", "upbeat",
    "vibrant", "vigilant", "vigorous", "wizardly", "wonderful",
    "xenodochial", "youthful", "zealous", "zen",
)

SURNAMES: tuple[str, ...] = (
    "albattani", "allen", "almeida", "agnesi", "archimedes",
    "ardinghelli", "aryabhata", "austin", "babbage", "banach",
    "banzai", "bardeen", "bartik", "bassi", "beaver",
    "bell", "benz", "bhabha", "bhaskara", "blackburn",
    "blackwell", "bohr", "booth", "borg", "bose",
    "boyd", "brahmagupta", "brattain", "brown", "burnell",
    "buck", "carson", "cartwright", "chandrasekhar", "chaplygin",
    "chatelet", "chatterjee", "chebyshev", "cocks", "cohen",
    "chaum", "clarke", "colden", "cori", "cray",
    "curran", "curie", "darwin", "davinci", "dewdney",
    "dhawan", "diffie", "dijkstra", "dirac", "driscoll",
    "dubinsky", "easley", "edison", "einstein", "elbakyan",
    "elgamal", "elion", "ellis", "engelbart", "euclid",
    "euler", "feistel", "fermat", "fermi", "feynman",
    "franklin", "gagarin", "galileo", "galois", "ganguly",
    "gates", "gauss", "germain", "goldberg", "goldstine",
    "goldwasser", "golick", "goodall", "greider", "grothendieck",
    "haibt", "hamilton", "haslett", "hawking", "hellman",
    "heisenberg", "hermann", "herschel", "hertz", "heyrovsky",
    "hodgkin", "hofstadter", "hoover", "hopper", "hugle",
    "hypatia", "ishizaka", "jackson", "jang", "jennings",
    "jepsen", "johnson", "joliot", "jones", "kalam",
    "kapitsa", "kare", "keldysh", "keller", "kepler",
    "khayyam", "khorana", "kilby", "kirch", "knuth",
    "kowalevski", "lalande", "lamarr", "lamport", "leakey",
    "leavitt", "lederberg", "lehmann", "lewin", "lichterman",
    "liskov", "lovelace", "lumiere", "mahavira", "margulis",
    "matsumoto", "mayer", "mccarthy", "mcclintock", "mclaren",
    "mclean", "mcnulty", "mendel", "mendeleev", "meitner",
    "meninsky", "merkle", "mestorf", "minsky", "mirzakhani",
    "morse", "murdock", "napier", "nash", "neumann",
    "newton", "nightingale", "nobel", "noether", "northcutt",
    "noyce", "panini", "pare", "pasteur", "payne",
    "perlman", "pike", "poincare", "poitras", "proskuriakova",
    "ptolemy", "raman", "ramanujan", "ride", "montalcini",
    "ritchie", "robinson", "roentgen", "rosalind", "rubin",
    "saha", "sammet", "sanderson", "shannon", "shaw",
    "shirley", "shockley", "shtern", "sinoussi", "snyder",
    "spence", "stallman", "stonebraker", "swanson", "swartz",
    "swirles", "taussig", "tereshkova", "tesla", "tharp",
    "thompson", "torvalds", "tu", "turing", "varahamihira",
    "vaughan", "visvesvaraya", "volhard", "villani", "wescoff",
    "wiles", "williams", "williamson", "wilson", "wing",
    "wozniak", "wright", "wu", "yalow", "yonath",
)


class NamesGenerator:
    """Generates deterministic human-readable names from hash values."""

    def __init__(self, adjectives: tuple[str, ...] = ADJECTIVES, surnames: tuple[str, ...] = SURNAMES):
        self._adjectives = adjectives
        self._surnames = surnames

    @lru_cache(maxsize=1024)
    def generate(self, hex_hash: str) -> str:
        """Generate a deterministic name from a hex hash string.

        Uses first 4 bytes (8 hex chars) of the hash to select adjective and surname.

        Args:
            hex_hash: Hexadecimal hash string (at least 8 characters)

        Returns:
            Human-readable name in format "adjective_surname"
        """
        assert len(hex_hash) >= 8, f"Hash must be at least 8 hex characters, got {len(hex_hash)}"

        adj_index = int(hex_hash[:4], 16) % len(self._adjectives)
        surname_index = int(hex_hash[4:8], 16) % len(self._surnames)

        return f"{self._adjectives[adj_index]}_{self._surnames[surname_index]}"


@lru_cache(maxsize=1)
def get_default_generator() -> NamesGenerator:
    """Get the default singleton NamesGenerator instance."""
    return NamesGenerator()


def generate_name(hex_hash: str) -> str:
    """Generate a deterministic name from a hex hash using the default generator.

    Args:
        hex_hash: Hexadecimal hash string (at least 8 characters)

    Returns:
        Human-readable name in format "adjective_surname"
    """
    return get_default_generator().generate(hex_hash)
