import re
import unicodedata

_JA_DROP = re.compile(r"[\s。、．，.,!?！？「」『』\"'“”‘’・…~〜]+")


def norm_ja(s):
    s = unicodedata.normalize("NFKC", s or "")
    return _JA_DROP.sub("", s)


def norm_en(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([.,!?;:])", r"\1", s)
    return s.rstrip(".!? ")


def same(lang, a, b):
    if lang == "ja":
        return norm_ja(a) == norm_ja(b) and norm_ja(a) != ""
    return norm_en(a) == norm_en(b) and norm_en(a) != ""
