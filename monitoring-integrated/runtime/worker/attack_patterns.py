"""공격 시그니처 룰 — attack_type 분류용 패턴 모음 (한 파일에서 관리).

우선순위: sqli > xss > path_traversal > sensitive_file > scanner > normal
- 복수 매칭 시 위 순서대로 '하나만' 채택.
- URI는 URL 디코딩 2회 + 소문자화 후 매칭(이중 인코딩 우회 방어).
- User-Agent는 소문자화 후 매칭(스캐너 탐지).
- Bedrock 미사용, 순수 정규식.
"""
import re
from urllib.parse import unquote_plus

# 복수 매칭 시 위에서부터 하나만 채택
PRIORITY = ("sqli", "xss", "path_traversal", "sensitive_file", "scanner")

# URI(경로+쿼리, 디코딩·소문자화된 문자열)에 매칭
URI_PATTERNS = {
    "sqli": [
        r"union\s+select", r"\bselect\b.+\bfrom\b", r"\bor\s+1\s*=\s*1\b",
        r"'\s*or\s*'", r"'\s*--", r"sleep\s*\(", r"benchmark\s*\(",
        r"waitfor\s+delay", r"information_schema", r"xp_cmdshell",
        r"concat\s*\(", r"@@version",
    ],
    "xss": [
        r"<script", r"</script", r"javascript:", r"onerror\s*=",
        r"onload\s*=", r"onmouseover\s*=", r"<svg", r"<iframe",
        r"<img[^>]+src", r"alert\s*\(", r"document\.cookie",
    ],
    "path_traversal": [
        r"\.\./", r"\.\.\\", r"\.\.\.\./", r"/etc/passwd",
        r"/proc/self/environ", r"c:\\windows", r"/windows/win\.ini",
    ],
    "sensitive_file": [
        r"/\.env\b", r"/\.git", r"/\.aws/credentials", r"\.kdbx\b",
        r"/wp-config\.php", r"\.htpasswd", r"id_rsa", r"web\.config",
        r"\.bak\b", r"\.sql\b", r"backup\.(zip|tar|sql)", r"/package\.json",
    ],
}

# User-Agent에 매칭 (스캐너/공격 도구)
UA_PATTERNS = {
    "scanner": [
        r"sqlmap", r"nikto", r"nmap", r"masscan", r"zap", r"zaproxy",
        r"owasp", r"dirbuster", r"gobuster", r"feroxbuster", r"wpscan",
        r"acunetix", r"nessus", r"nuclei", r"hydra", r"wfuzz", r"burp",
    ],
}

_URI = {k: [re.compile(p) for p in v] for k, v in URI_PATTERNS.items()}
_UA = {k: [re.compile(p) for p in v] for k, v in UA_PATTERNS.items()}


def _decode(s):
    for _ in range(2):            # URL 디코딩 최대 2회 (+ 는 공백으로: union+select 대응)
        s = unquote_plus(s)
    return s.lower()


def classify(uri="", user_agent=""):
    """우선순위 순서대로 하나만 반환. 매칭 없으면 'normal'."""
    u = _decode(uri or "")
    ua = (user_agent or "").lower()
    for atype in PRIORITY:
        if any(p.search(u) for p in _URI.get(atype, [])):
            return atype
        if any(p.search(ua) for p in _UA.get(atype, [])):
            return atype
    return "normal"
