"""Lexer for Cowgol language."""

from typing import Iterator, Optional, List
from dataclasses import dataclass
from .tokens import Token, TokenType, SourceLocation, KEYWORDS


class LexerError(Exception):
    """Error during lexical analysis."""
    def __init__(self, message: str, location: SourceLocation):
        self.location = location
        super().__init__(f"{location}: {message}")


class Lexer:
    """Tokenizer for Cowgol source code."""

    def __init__(self, source: str, filename: str = "<input>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.column = 1
        self.include_paths: List[str] = []

    def add_include_path(self, path: str) -> None:
        """Add a path to search for include files."""
        self.include_paths.append(path)

    def _location(self) -> SourceLocation:
        """Get current source location."""
        return SourceLocation(self.filename, self.line, self.column)

    def _peek(self, offset: int = 0) -> str:
        """Look at character at current position + offset."""
        pos = self.pos + offset
        if pos >= len(self.source):
            return '\0'
        return self.source[pos]

    def _advance(self) -> str:
        """Consume and return current character."""
        ch = self._peek()
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return ch

    def _skip_whitespace_and_comments(self) -> None:
        """Skip whitespace and # comments."""
        while True:
            ch = self._peek()
            if ch in ' \t\r':
                self._advance()
            elif ch == '\n':
                self._advance()
            elif ch == '#':
                # Comment until end of line
                while self._peek() not in ('\n', '\0'):
                    self._advance()
            else:
                break

    def _read_number(self) -> Token:
        """Read a numeric literal."""
        loc = self._location()
        start = self.pos

        # Check for base prefix
        base = 10
        if self._peek() == '0' and self._peek(1) in 'xXoObBdD':
            self._advance()  # '0'
            prefix = self._advance().lower()
            if prefix == 'x':
                base = 16
            elif prefix == 'o':
                base = 8
            elif prefix == 'b':
                base = 2
            elif prefix == 'd':
                base = 10
            start = self.pos

        # Read digits (allowing underscores)
        digits = ''
        while True:
            ch = self._peek()
            if ch == '_':
                self._advance()
                continue
            if base == 16 and ch.lower() in '0123456789abcdef':
                digits += self._advance()
            elif base == 10 and ch in '0123456789':
                digits += self._advance()
            elif base == 8 and ch in '01234567':
                digits += self._advance()
            elif base == 2 and ch in '01':
                digits += self._advance()
            else:
                break

        if not digits:
            raise LexerError(f"Expected digits after base prefix", loc)

        try:
            value = int(digits, base)
        except ValueError:
            raise LexerError(f"Invalid number literal", loc)

        return Token(TokenType.NUMBER, value, loc)

    def _read_string(self) -> Token:
        """Read a string literal."""
        loc = self._location()
        quote = self._advance()  # Opening quote
        chars = []

        while True:
            ch = self._peek()
            if ch == '\0':
                raise LexerError("Unterminated string literal", loc)
            if ch == '\n':
                raise LexerError("Newline in string literal", loc)
            if ch == quote:
                self._advance()
                break
            if ch == '\\':
                self._advance()
                escape = self._advance()
                if escape == 'n':
                    chars.append('\n')
                elif escape == 'r':
                    chars.append('\r')
                elif escape == 't':
                    chars.append('\t')
                elif escape == '\\':
                    chars.append('\\')
                elif escape == '"':
                    chars.append('"')
                elif escape == "'":
                    chars.append("'")
                elif escape == '0':
                    chars.append('\0')
                else:
                    raise LexerError(f"Unknown escape sequence \\{escape}", self._location())
            else:
                chars.append(self._advance())

        return Token(TokenType.STRING, ''.join(chars), loc)

    def _read_char(self) -> Token:
        """Read a character literal."""
        loc = self._location()
        self._advance()  # Opening quote

        ch = self._peek()
        if ch == '\\':
            self._advance()
            escape = self._advance()
            if escape == 'n':
                value = ord('\n')
            elif escape == 'r':
                value = ord('\r')
            elif escape == 't':
                value = ord('\t')
            elif escape == '\\':
                value = ord('\\')
            elif escape == "'":
                value = ord("'")
            elif escape == '0':
                value = 0
            else:
                raise LexerError(f"Unknown escape sequence \\{escape}", loc)
        elif ch == "'":
            raise LexerError("Empty character literal", loc)
        else:
            value = ord(self._advance())

        if self._peek() != "'":
            raise LexerError("Expected closing quote", self._location())
        self._advance()

        return Token(TokenType.NUMBER, value, loc)  # Char literals are numbers

    def _read_identifier(self) -> Token:
        """Read an identifier or keyword."""
        loc = self._location()
        chars = []

        while True:
            ch = self._peek()
            if ch.isalnum() or ch == '_':
                chars.append(self._advance())
            else:
                break

        name = ''.join(chars)

        # Check if it's a keyword
        if name in KEYWORDS:
            return Token(KEYWORDS[name], name, loc)

        return Token(TokenType.ID, name, loc)

    def next_token(self) -> Token:
        """Get the next token from the source."""
        self._skip_whitespace_and_comments()

        loc = self._location()
        ch = self._peek()

        if ch == '\0':
            return Token(TokenType.EOF, None, loc)

        # Numbers
        if ch.isdigit():
            return self._read_number()

        # Strings
        if ch == '"':
            return self._read_string()

        # Character literals
        if ch == "'":
            return self._read_char()

        # Identifiers and keywords
        if ch.isalpha() or ch == '_':
            return self._read_identifier()

        # Two-character operators
        ch2 = self._peek(1)
        two_char = ch + ch2

        if two_char == ':=':
            self._advance()
            self._advance()
            return Token(TokenType.ASSIGN, ':=', loc)
        if two_char == '==':
            self._advance()
            self._advance()
            return Token(TokenType.EQ, '==', loc)
        if two_char == '!=':
            self._advance()
            self._advance()
            return Token(TokenType.NE, '!=', loc)
        if two_char == '<=':
            self._advance()
            self._advance()
            return Token(TokenType.LE, '<=', loc)
        if two_char == '>=':
            self._advance()
            self._advance()
            return Token(TokenType.GE, '>=', loc)
        if two_char == '<<':
            self._advance()
            self._advance()
            return Token(TokenType.LSHIFT, '<<', loc)
        if two_char == '>>':
            self._advance()
            self._advance()
            return Token(TokenType.RSHIFT, '>>', loc)

        # Single-character tokens
        single_char_tokens = {
            '+': TokenType.PLUS,
            '-': TokenType.MINUS,
            '*': TokenType.STAR,
            '/': TokenType.SLASH,
            '%': TokenType.PERCENT,
            '&': TokenType.AMPERSAND,
            '|': TokenType.PIPE,
            '^': TokenType.CARET,
            '~': TokenType.TILDE,
            '<': TokenType.LT,
            '>': TokenType.GT,
            '(': TokenType.LPAREN,
            ')': TokenType.RPAREN,
            '[': TokenType.LBRACKET,
            ']': TokenType.RBRACKET,
            '{': TokenType.LBRACE,
            '}': TokenType.RBRACE,
            ',': TokenType.COMMA,
            ':': TokenType.COLON,
            ';': TokenType.SEMICOLON,
            '.': TokenType.DOT,
            '@': TokenType.AT,
        }

        if ch in single_char_tokens:
            self._advance()
            return Token(single_char_tokens[ch], ch, loc)

        raise LexerError(f"Unexpected character: {ch!r}", loc)

    def tokenize(self) -> Iterator[Token]:
        """Generate all tokens from source."""
        while True:
            token = self.next_token()
            yield token
            if token.type == TokenType.EOF:
                break


def tokenize_file(filename: str) -> Iterator[Token]:
    """Tokenize a file."""
    with open(filename, 'r') as f:
        source = f.read()
    lexer = Lexer(source, filename)
    return lexer.tokenize()
