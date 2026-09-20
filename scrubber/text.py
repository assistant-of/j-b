"""Conservative text extraction for quoted LaTeX fragments; never executes TeX."""
import re


FORMATTING = {"textbf", "textit", "textsl", "textsc", "textrm", "textsf",
              "texttt", "textnormal", "emph", "underline", "mbox"}


def plain_quote(quote, source):
    """Remove only known formatting. Unknown or malformed TeX stays unchanged.

    Evidence is still checked against the raw source by the caller. This is not
    a general TeX renderer: math, custom macros and comments require plain-text
    source material or a smaller quote.
    """
    if not source.lower().endswith(".tex"):
        return quote
    parts, depth, pos = [], 0, 0
    while pos < len(quote):
        char = quote[pos]
        if char == "\\":
            if pos + 1 < len(quote) and quote[pos + 1] in "%&#_${}":
                parts.append(quote[pos + 1])
                pos += 2
                continue
            command = re.match(r"\\([a-zA-Z]+)\s*\{", quote[pos:])
            if not command or command[1] not in FORMATTING:
                return quote
            # Consume the command, leaving its opening brace for group handling.
            pos += command.end() - 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return quote
        elif char in "$%#&^_":
            return quote
        else:
            parts.append(" " if char == "~" else char)
        pos += 1
    return quote if depth else "".join(parts)
