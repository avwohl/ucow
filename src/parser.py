"""Recursive descent parser for Cowgol."""

from typing import List, Optional, Tuple, Callable
from .tokens import Token, TokenType, SourceLocation
from .lexer import Lexer, LexerError
from . import ast


class ParseError(Exception):
    """Error during parsing."""
    def __init__(self, message: str, location: SourceLocation):
        self.location = location
        super().__init__(f"{location}: {message}")


class Parser:
    """Recursive descent parser for Cowgol."""

    def __init__(self, lexer: Lexer):
        self.lexer = lexer
        self.current: Token = None
        self.peeked: Token = None
        self._advance()

    def _advance(self) -> Token:
        """Consume current token and get next."""
        prev = self.current
        if self.peeked:
            self.current = self.peeked
            self.peeked = None
        else:
            self.current = self.lexer.next_token()
        return prev

    def _peek_next(self) -> Token:
        """Look at next token without consuming current."""
        if not self.peeked:
            self.peeked = self.lexer.next_token()
        return self.peeked

    def _at(self, *types: TokenType) -> bool:
        """Check if current token is one of the given types."""
        return self.current.type in types

    def _at_keyword(self, *keywords: str) -> bool:
        """Check if current token is one of the given keywords."""
        if self.current.type == TokenType.ID:
            return self.current.value in keywords
        return False

    def _match(self, *types: TokenType) -> Optional[Token]:
        """Consume token if it matches, else return None."""
        if self._at(*types):
            return self._advance()
        return None

    def _expect(self, type: TokenType, message: str = None) -> Token:
        """Consume token of given type or raise error."""
        if not self._at(type):
            msg = message or f"Expected {type.name}"
            raise ParseError(msg, self.current.location)
        return self._advance()

    def _expect_keyword(self, keyword: str) -> Token:
        """Consume keyword or raise error."""
        if self.current.type == TokenType.ID and self.current.value == keyword:
            return self._advance()
        raise ParseError(f"Expected '{keyword}'", self.current.location)

    def _location(self) -> SourceLocation:
        """Get current source location."""
        return self.current.location

    # Type parsing

    def _parse_base_type(self) -> ast.Type:
        """Parse a base type (not array or pointer)."""
        loc = self._location()

        # Built-in scalar types
        scalar_types = {
            TokenType.INT8: 'int8',
            TokenType.UINT8: 'uint8',
            TokenType.INT16: 'int16',
            TokenType.UINT16: 'uint16',
            TokenType.INT32: 'int32',
            TokenType.UINT32: 'uint32',
            TokenType.INTPTR: 'intptr',
        }

        for tt, name in scalar_types.items():
            if self._match(tt):
                return ast.ScalarType(loc, name)

        # Pointer type: [T]
        if self._match(TokenType.LBRACKET):
            target = self._parse_type()
            self._expect(TokenType.RBRACKET, "Expected ']' after pointer target type")
            return ast.PointerType(loc, target)

        # @indexof/@sizeof as type expressions
        if self._match(TokenType.AT):
            op = self._expect(TokenType.ID, "Expected @ operator name").value
            target = self._expect(TokenType.ID, "Expected array/type name").value
            if op == 'indexof':
                return ast.IndexOfType(loc, target)
            elif op == 'sizeof':
                return ast.SizeOfType(loc, target)
            else:
                raise ParseError(f"Unknown type operator: @{op}", loc)

        # Named type (record, typedef)
        if self._at(TokenType.ID):
            name = self._advance().value
            # Handle ranged integer type: int(min, max)
            if name == 'int' and self._match(TokenType.LPAREN):
                # Parse min expression
                min_expr = self._parse_expression()
                self._expect(TokenType.COMMA, "Expected ',' in int(min, max)")
                # Parse max expression
                max_expr = self._parse_expression()
                self._expect(TokenType.RPAREN, "Expected ')' in int(min, max)")
                return ast.RangedIntType(loc, min_expr, max_expr)
            return ast.NamedType(loc, name)

        raise ParseError("Expected type", loc)

    def _parse_type(self) -> ast.Type:
        """Parse a type, including array types."""
        base = self._parse_base_type()

        # Array type: T[size]
        while self._match(TokenType.LBRACKET):
            if self._match(TokenType.RBRACKET):
                # Empty brackets - size inferred
                base = ast.ArrayType(base.location, base, None)
            else:
                size = self._parse_expression()
                self._expect(TokenType.RBRACKET, "Expected ']' after array size")
                base = ast.ArrayType(base.location, base, size)

        return base

    # Expression parsing (precedence climbing)

    def _parse_primary(self) -> ast.Expression:
        """Parse primary expression."""
        loc = self._location()

        # Number
        if self._at(TokenType.NUMBER):
            value = self._advance().value
            return ast.NumberLiteral(loc, value)

        # String
        if self._at(TokenType.STRING):
            value = self._advance().value
            return ast.StringLiteral(loc, value)

        # nil
        if self._match(TokenType.NIL):
            return ast.NilLiteral(loc)

        # Parenthesized expression or tuple
        if self._match(TokenType.LPAREN):
            expr = self._parse_expression()
            self._expect(TokenType.RPAREN, "Expected ')'")
            return expr

        # Array/record initializer
        if self._match(TokenType.LBRACE):
            elements = []
            if not self._at(TokenType.RBRACE):
                elements.append(self._parse_expression())
                while self._match(TokenType.COMMA):
                    # Allow trailing comma
                    if self._at(TokenType.RBRACE):
                        break
                    elements.append(self._parse_expression())
            self._expect(TokenType.RBRACE, "Expected '}'")
            return ast.ArrayInitializer(loc, elements)

        # Dereference: [expr]
        if self._match(TokenType.LBRACKET):
            expr = self._parse_expression()
            self._expect(TokenType.RBRACKET, "Expected ']'")
            return ast.Dereference(loc, expr)

        # @ operators
        if self._match(TokenType.AT):
            name = self._expect(TokenType.ID, "Expected @ operator name").value

            if name == 'sizeof':
                target = self._parse_unary()
                return ast.SizeOf(loc, target)
            elif name == 'bytesof':
                # @bytesof can take a type name or expression
                # Check for type keywords first
                scalar_types = {
                    TokenType.INT8: 'int8', TokenType.UINT8: 'uint8',
                    TokenType.INT16: 'int16', TokenType.UINT16: 'uint16',
                    TokenType.INT32: 'int32', TokenType.UINT32: 'uint32',
                    TokenType.INTPTR: 'intptr',
                }
                for tt, tname in scalar_types.items():
                    if self._match(tt):
                        return ast.BytesOf(loc, ast.Identifier(loc, tname))
                target = self._parse_unary()
                return ast.BytesOf(loc, target)
            elif name == 'indexof':
                target = self._parse_unary()
                return ast.IndexOf(loc, target)
            elif name == 'next':
                target = self._parse_unary()
                return ast.Next(loc, target)
            elif name == 'prev':
                target = self._parse_unary()
                return ast.Prev(loc, target)
            else:
                raise ParseError(f"Unknown @ operator: {name}", loc)

        # Identifier
        if self._at(TokenType.ID):
            name = self._advance().value
            return ast.Identifier(loc, name)

        raise ParseError("Expected expression", loc)

    def _parse_postfix_continue(self, expr: ast.Expression) -> ast.Expression:
        """Continue parsing postfix expressions from an existing expression."""
        while True:
            loc = self._location()

            # Function call
            if self._match(TokenType.LPAREN):
                args = []
                if not self._at(TokenType.RPAREN):
                    args.append(self._parse_expression())
                    while self._match(TokenType.COMMA):
                        args.append(self._parse_expression())
                self._expect(TokenType.RPAREN, "Expected ')'")
                expr = ast.Call(loc, expr, args)

            # Array subscript
            elif self._match(TokenType.LBRACKET):
                index = self._parse_expression()
                self._expect(TokenType.RBRACKET, "Expected ']'")
                expr = ast.ArrayAccess(loc, expr, index)

            # Field access
            elif self._match(TokenType.DOT):
                field = self._expect(TokenType.ID, "Expected field name").value
                expr = ast.FieldAccess(loc, expr, field)

            else:
                break

        return expr

    def _parse_postfix(self) -> ast.Expression:
        """Parse postfix expressions (calls, subscripts, field access)."""
        expr = self._parse_primary()
        return self._parse_postfix_continue(expr)

    def _parse_unary(self) -> ast.Expression:
        """Parse unary expressions."""
        loc = self._location()

        # Address-of
        if self._match(TokenType.AMPERSAND):
            return ast.AddressOf(loc, self._parse_unary())

        # Unary minus
        if self._match(TokenType.MINUS):
            return ast.UnaryOp(loc, '-', self._parse_unary())

        # Bitwise not
        if self._match(TokenType.TILDE):
            return ast.UnaryOp(loc, '~', self._parse_unary())

        # Logical not
        if self._match(TokenType.NOT):
            return ast.NotOp(loc, self._parse_unary())

        return self._parse_postfix()

    def _parse_cast(self) -> ast.Expression:
        """Parse cast expressions."""
        expr = self._parse_unary()

        while self._match(TokenType.AS):
            loc = self._location()
            target_type = self._parse_type()
            expr = ast.Cast(loc, expr, target_type)

        return expr

    def _parse_multiplicative(self) -> ast.Expression:
        """Parse multiplicative expressions."""
        left = self._parse_cast()

        while self._at(TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            loc = self._location()
            op = self._advance().value
            right = self._parse_cast()
            left = ast.BinaryOp(loc, op, left, right)

        return left

    def _parse_additive(self) -> ast.Expression:
        """Parse additive expressions."""
        left = self._parse_multiplicative()

        while self._at(TokenType.PLUS, TokenType.MINUS):
            loc = self._location()
            op = self._advance().value
            right = self._parse_multiplicative()
            left = ast.BinaryOp(loc, op, left, right)

        return left

    def _parse_shift(self) -> ast.Expression:
        """Parse shift expressions."""
        left = self._parse_additive()

        while self._at(TokenType.LSHIFT, TokenType.RSHIFT):
            loc = self._location()
            op = self._advance().value
            right = self._parse_additive()
            left = ast.BinaryOp(loc, op, left, right)

        return left

    def _parse_bitwise_and(self) -> ast.Expression:
        """Parse bitwise AND expressions."""
        left = self._parse_shift()

        while self._match(TokenType.AMPERSAND):
            loc = self._location()
            right = self._parse_shift()
            left = ast.BinaryOp(loc, '&', left, right)

        return left

    def _parse_bitwise_xor(self) -> ast.Expression:
        """Parse bitwise XOR expressions."""
        left = self._parse_bitwise_and()

        while self._match(TokenType.CARET):
            loc = self._location()
            right = self._parse_bitwise_and()
            left = ast.BinaryOp(loc, '^', left, right)

        return left

    def _parse_bitwise_or(self) -> ast.Expression:
        """Parse bitwise OR expressions."""
        left = self._parse_bitwise_xor()

        while self._match(TokenType.PIPE):
            loc = self._location()
            right = self._parse_bitwise_xor()
            left = ast.BinaryOp(loc, '|', left, right)

        return left

    def _parse_comparison(self) -> ast.Expression:
        """Parse comparison expressions."""
        left = self._parse_bitwise_or()

        if self._at(TokenType.EQ, TokenType.NE, TokenType.LT,
                    TokenType.LE, TokenType.GT, TokenType.GE):
            loc = self._location()
            op = self._advance().value
            right = self._parse_bitwise_or()
            return ast.Comparison(loc, op, left, right)

        return left

    def _parse_logical_not(self) -> ast.Expression:
        """Parse logical NOT expressions."""
        if self._match(TokenType.NOT):
            loc = self._location()
            return ast.NotOp(loc, self._parse_logical_not())
        return self._parse_comparison()

    def _parse_logical_and(self) -> ast.Expression:
        """Parse logical AND expressions."""
        left = self._parse_logical_not()

        while self._match(TokenType.AND):
            loc = self._location()
            right = self._parse_logical_not()
            left = ast.LogicalOp(loc, 'and', left, right)

        return left

    def _parse_logical_or(self) -> ast.Expression:
        """Parse logical OR expressions."""
        left = self._parse_logical_and()

        while self._match(TokenType.OR):
            loc = self._location()
            right = self._parse_logical_and()
            left = ast.LogicalOp(loc, 'or', left, right)

        return left

    def _parse_expression(self) -> ast.Expression:
        """Parse an expression."""
        return self._parse_logical_or()

    # Statement parsing

    def _parse_block(self, end_keywords: List[str]) -> List[ast.Statement]:
        """Parse statements until one of the end keywords."""
        stmts = []
        while True:
            # Check for 'end' token
            if self._at(TokenType.END):
                break
            # Check for keyword IDs like 'elseif', 'else', 'when'
            if self.current.type == TokenType.ELSEIF or self.current.type == TokenType.ELSE:
                break
            if self.current.type == TokenType.WHEN:
                break
            if self.current.type == TokenType.ID and self.current.value in end_keywords:
                break
            if self._at(TokenType.EOF):
                raise ParseError(f"Expected one of: {end_keywords}", self._location())
            stmts.append(self._parse_statement())
        return stmts

    def _parse_var_decl(self) -> ast.VarDecl:
        """Parse variable declaration."""
        loc = self._location()
        self._expect(TokenType.VAR, "Expected 'var'")

        name = self._expect(TokenType.ID, "Expected variable name").value

        var_type = None
        init = None

        if self._match(TokenType.COLON):
            var_type = self._parse_type()

        if self._match(TokenType.ASSIGN):
            init = self._parse_expression()

        self._match(TokenType.SEMICOLON)
        return ast.VarDecl(loc, name, var_type, init)

    def _parse_const_decl(self) -> ast.ConstDecl:
        """Parse constant declaration."""
        loc = self._location()
        self._expect(TokenType.CONST, "Expected 'const'")

        name = self._expect(TokenType.ID, "Expected constant name").value
        self._expect(TokenType.ASSIGN, "Expected ':=' in const declaration")
        value = self._parse_expression()

        self._match(TokenType.SEMICOLON)
        return ast.ConstDecl(loc, name, value)

    def _parse_if(self) -> ast.IfStmt:
        """Parse if statement."""
        loc = self._location()
        self._expect(TokenType.IF, "Expected 'if'")

        condition = self._parse_expression()
        self._expect(TokenType.THEN, "Expected 'then'")

        then_body = self._parse_block(['elseif', 'else', 'end'])

        elseifs = []
        while self._at(TokenType.ELSEIF):
            self._advance()
            elif_cond = self._parse_expression()
            self._expect(TokenType.THEN, "Expected 'then'")
            elif_body = self._parse_block(['elseif', 'else', 'end'])
            elseifs.append((elif_cond, elif_body))

        else_body = None
        if self._match(TokenType.ELSE):
            else_body = self._parse_block(['end'])

        self._expect(TokenType.END, "Expected 'end'")
        self._expect(TokenType.IF, "Expected 'if' after 'end'")
        self._match(TokenType.SEMICOLON)

        return ast.IfStmt(loc, condition, then_body, elseifs, else_body)

    def _parse_while(self) -> ast.WhileStmt:
        """Parse while loop."""
        loc = self._location()
        self._expect(TokenType.WHILE, "Expected 'while'")

        condition = self._parse_expression()
        self._expect(TokenType.LOOP, "Expected 'loop'")
        self._match(TokenType.SEMICOLON)  # Optional semicolon after loop

        body = self._parse_block(['end'])
        self._expect(TokenType.END, "Expected 'end'")
        self._expect(TokenType.LOOP, "Expected 'loop'")
        self._match(TokenType.SEMICOLON)

        return ast.WhileStmt(loc, condition, body)

    def _parse_loop(self) -> ast.LoopStmt:
        """Parse infinite loop."""
        loc = self._location()
        self._expect(TokenType.LOOP, "Expected 'loop'")
        self._match(TokenType.SEMICOLON)  # Optional semicolon after loop

        body = self._parse_block(['end'])
        self._expect(TokenType.END, "Expected 'end'")
        self._expect(TokenType.LOOP, "Expected 'loop'")
        self._match(TokenType.SEMICOLON)

        return ast.LoopStmt(loc, body)

    def _parse_case(self) -> ast.CaseStmt:
        """Parse case statement."""
        loc = self._location()
        self._expect(TokenType.CASE, "Expected 'case'")

        expr = self._parse_expression()
        self._expect(TokenType.IS, "Expected 'is'")

        whens = []
        else_body = None

        while self._match(TokenType.WHEN):
            if self._at(TokenType.ELSE) or self._at_keyword('else'):
                self._advance()
                self._expect(TokenType.COLON, "Expected ':'")
                else_body = self._parse_block(['when', 'end'])
            else:
                values = [self._parse_expression()]
                while self._match(TokenType.COMMA):
                    values.append(self._parse_expression())
                self._expect(TokenType.COLON, "Expected ':'")
                body = self._parse_block(['when', 'end'])
                whens.append((values, body))

        self._expect(TokenType.END, "Expected 'end'")
        self._expect(TokenType.CASE, "Expected 'case' after 'end'")
        self._match(TokenType.SEMICOLON)

        return ast.CaseStmt(loc, expr, whens, else_body)

    def _parse_return(self) -> ast.ReturnStmt:
        """Parse return statement."""
        loc = self._location()
        self._expect(TokenType.RETURN, "Expected 'return'")
        self._match(TokenType.SEMICOLON)
        return ast.ReturnStmt(loc)

    def _parse_break(self) -> ast.BreakStmt:
        """Parse break statement."""
        loc = self._location()
        self._expect(TokenType.BREAK, "Expected 'break'")
        self._match(TokenType.SEMICOLON)
        return ast.BreakStmt(loc)

    def _parse_continue(self) -> ast.ContinueStmt:
        """Parse continue statement."""
        loc = self._location()
        self._expect(TokenType.CONTINUE, "Expected 'continue'")
        self._match(TokenType.SEMICOLON)
        return ast.ContinueStmt(loc)

    def _parse_asm(self) -> ast.AsmStmt:
        """Parse inline assembly."""
        loc = self._location()
        self._expect(TokenType.AT, "Expected '@'")
        self._expect_keyword('asm')

        parts = []
        # First part is always a string
        parts.append(self._expect(TokenType.STRING, "Expected assembly string").value)

        while self._match(TokenType.COMMA):
            if self._at(TokenType.STRING):
                parts.append(self._advance().value)
            else:
                parts.append(self._parse_expression())

        self._match(TokenType.SEMICOLON)
        return ast.AsmStmt(loc, parts)

    def _parse_assignment_or_call(self) -> ast.Statement:
        """Parse assignment or expression statement."""
        loc = self._location()

        # Check for multi-assignment: (a, b) := call()
        # Or indirect call: (expr)(args)
        if self._match(TokenType.LPAREN):
            targets = [self._parse_expression()]
            while self._match(TokenType.COMMA):
                targets.append(self._parse_expression())
            self._expect(TokenType.RPAREN)

            # If we see ':=', this is multi-assignment
            if self._match(TokenType.ASSIGN):
                value = self._parse_expression()
                self._match(TokenType.SEMICOLON)
                if not isinstance(value, ast.Call):
                    raise ParseError("Multi-assignment requires a call", value.location)
                return ast.MultiAssignment(loc, targets, value)

            # Otherwise, this is a parenthesized expression (possibly a call)
            # Reconstruct and continue parsing as postfix/expression
            if len(targets) != 1:
                raise ParseError("Expected ':=' after tuple", loc)
            expr = targets[0]
            # Continue parsing postfix operations (call, subscript, field access)
            expr = self._parse_postfix_continue(expr)
            if self._match(TokenType.ASSIGN):
                value = self._parse_expression()
                self._match(TokenType.SEMICOLON)
                return ast.Assignment(loc, expr, value)
            self._match(TokenType.SEMICOLON)
            return ast.ExprStmt(loc, expr)

        # Regular expression/assignment
        expr = self._parse_expression()

        if self._match(TokenType.ASSIGN):
            value = self._parse_expression()
            self._match(TokenType.SEMICOLON)
            return ast.Assignment(loc, expr, value)

        self._match(TokenType.SEMICOLON)
        return ast.ExprStmt(loc, expr)

    def _parse_statement(self) -> ast.Statement:
        """Parse a statement."""
        # Declarations that can appear in statement context
        if self._at(TokenType.VAR):
            return self._parse_var_decl()
        if self._at(TokenType.CONST):
            return self._parse_const_decl()

        # Control flow
        if self._at(TokenType.IF):
            return self._parse_if()
        if self._at(TokenType.WHILE):
            return self._parse_while()
        if self._at(TokenType.CASE):
            return self._parse_case()
        if self._at(TokenType.RETURN):
            return self._parse_return()
        if self._at(TokenType.BREAK):
            return self._parse_break()
        if self._at(TokenType.CONTINUE):
            return self._parse_continue()

        # Keyword-triggered statements
        if self._at(TokenType.LOOP):
            return self._parse_loop()

        # Inline assembly
        if self._at(TokenType.AT):
            next_tok = self._peek_next()
            if next_tok.type == TokenType.ID and next_tok.value == 'asm':
                return self._parse_asm()

        # Nested subroutine
        if self._at(TokenType.SUB):
            loc = self._location()
            sub = self._parse_sub()
            return ast.NestedSubStmt(loc, sub)

        # Local interface definition
        if self._at(TokenType.INTERFACE):
            return self._parse_interface()

        # Assignment or call
        return self._parse_assignment_or_call()

    # Declaration parsing

    def _parse_parameter_list(self) -> Tuple[List[ast.Parameter], List[ast.Parameter]]:
        """Parse subroutine parameters and returns."""
        params = []
        returns = []

        self._expect(TokenType.LPAREN, "Expected '('")

        if not self._at(TokenType.RPAREN):
            # First parameter
            name = self._expect(TokenType.ID, "Expected parameter name").value
            self._expect(TokenType.COLON, "Expected ':'")
            ptype = self._parse_type()
            params.append(ast.Parameter(self._location(), name, ptype))

            while self._match(TokenType.COMMA):
                name = self._expect(TokenType.ID, "Expected parameter name").value
                self._expect(TokenType.COLON, "Expected ':'")
                ptype = self._parse_type()
                params.append(ast.Parameter(self._location(), name, ptype))

        self._expect(TokenType.RPAREN, "Expected ')'")

        # Return parameters
        if self._match(TokenType.COLON):
            self._expect(TokenType.LPAREN, "Expected '('")

            name = self._expect(TokenType.ID, "Expected return parameter name").value
            self._expect(TokenType.COLON, "Expected ':'")
            rtype = self._parse_type()
            returns.append(ast.Parameter(self._location(), name, rtype))

            while self._match(TokenType.COMMA):
                name = self._expect(TokenType.ID, "Expected return parameter name").value
                self._expect(TokenType.COLON, "Expected ':'")
                rtype = self._parse_type()
                returns.append(ast.Parameter(self._location(), name, rtype))

            self._expect(TokenType.RPAREN, "Expected ')'")

        return params, returns

    def _parse_sub(self, is_decl: bool = False, is_impl: bool = False) -> ast.SubDecl:
        """Parse subroutine declaration."""
        loc = self._location()
        self._expect(TokenType.SUB, "Expected 'sub'")

        name = self._expect(TokenType.ID, "Expected subroutine name").value

        # For @impl, parameters come from the declaration
        if is_impl:
            params = []
            returns = []
        else:
            # Parameter list is optional for interface implementations
            if self._at(TokenType.LPAREN):
                params, returns = self._parse_parameter_list()
            else:
                params = []
                returns = []

        extern_name = None
        implements = None

        # Parse attributes
        while self._at(TokenType.AT):
            self._advance()
            attr = self._expect(TokenType.ID, "Expected attribute name").value

            if attr == 'extern':
                self._expect(TokenType.LPAREN, "Expected '('")
                extern_name = self._expect(TokenType.STRING, "Expected extern name").value
                self._expect(TokenType.RPAREN, "Expected ')'")
            else:
                raise ParseError(f"Unknown attribute: {attr}", self._location())

        # implements clause
        if self._match(TokenType.IMPLEMENTS):
            implements = self._expect(TokenType.ID, "Expected interface name").value

        body = None
        if is_decl:
            # Forward declaration ends with semicolon
            self._match(TokenType.SEMICOLON)
        elif self._match(TokenType.IS):
            # Has a body
            body = []
            while not self._at(TokenType.END):
                if self._at(TokenType.EOF):
                    raise ParseError("Expected 'end sub'", self._location())
                # Handle nested declarations
                if self._at(TokenType.SUB):
                    body.append(self._parse_sub())
                elif self._at(TokenType.RECORD):
                    body.append(self._parse_record())
                elif self._at(TokenType.TYPEDEF):
                    body.append(self._parse_typedef())
                elif self._at(TokenType.AT):
                    # Could be @decl, @impl, or @asm
                    next_tok = self._peek_next()
                    if next_tok.type == TokenType.ID:
                        if next_tok.value == 'decl':
                            self._advance()  # @
                            self._advance()  # decl
                            body.append(self._parse_sub(is_decl=True))
                        elif next_tok.value == 'impl':
                            self._advance()  # @
                            self._advance()  # impl
                            body.append(self._parse_sub(is_impl=True))
                        elif next_tok.value == 'asm':
                            body.append(self._parse_asm())
                        else:
                            body.append(self._parse_statement())
                    else:
                        body.append(self._parse_statement())
                else:
                    body.append(self._parse_statement())

            self._expect(TokenType.END, "Expected 'end'")
            self._expect(TokenType.SUB, "Expected 'sub' after 'end'")
            self._match(TokenType.SEMICOLON)
        else:
            # External declaration with no body
            self._match(TokenType.SEMICOLON)

        return ast.SubDecl(loc, name, params, returns, body,
                          extern_name, implements, is_decl, is_impl)

    def _parse_record(self) -> ast.RecordDecl:
        """Parse record declaration."""
        loc = self._location()
        self._expect(TokenType.RECORD, "Expected 'record'")

        name = self._expect(TokenType.ID, "Expected record name").value

        base = None
        if self._match(TokenType.COLON):
            base = self._expect(TokenType.ID, "Expected base record name").value

        self._expect(TokenType.IS, "Expected 'is'")

        fields = []
        while not self._at(TokenType.END):
            if self._at(TokenType.EOF):
                raise ParseError("Expected 'end record'", self._location())

            field_loc = self._location()
            field_name = self._expect(TokenType.ID, "Expected field name").value

            # Optional @at() for explicit offset
            offset = None
            if self._match(TokenType.AT):
                # Support both @(n) and @at(n) syntax
                if self._at(TokenType.ID) and self.current.value == 'at':
                    self._advance()  # consume 'at'
                self._expect(TokenType.LPAREN, "Expected '('")
                offset = self._expect(TokenType.NUMBER, "Expected offset").value
                self._expect(TokenType.RPAREN, "Expected ')'")

            self._expect(TokenType.COLON, "Expected ':'")
            field_type = self._parse_type()
            self._match(TokenType.SEMICOLON)

            fields.append(ast.RecordField(field_loc, field_name, field_type, offset))

        self._expect(TokenType.END, "Expected 'end'")
        self._expect(TokenType.RECORD, "Expected 'record' after 'end'")
        self._match(TokenType.SEMICOLON)

        record_type = ast.RecordType(loc, name, fields, base)
        return ast.RecordDecl(loc, record_type)

    def _parse_typedef(self) -> ast.TypedefDecl:
        """Parse typedef declaration."""
        loc = self._location()
        self._expect(TokenType.TYPEDEF, "Expected 'typedef'")

        name = self._expect(TokenType.ID, "Expected type name").value
        self._expect(TokenType.IS, "Expected 'is'")
        type_def = self._parse_type()
        self._match(TokenType.SEMICOLON)

        return ast.TypedefDecl(loc, name, type_def)

    def _parse_interface(self) -> ast.InterfaceDecl:
        """Parse interface declaration."""
        loc = self._location()
        self._expect(TokenType.INTERFACE, "Expected 'interface'")

        name = self._expect(TokenType.ID, "Expected interface name").value
        params, returns = self._parse_parameter_list()
        self._match(TokenType.SEMICOLON)

        interface = ast.InterfaceType(loc, name, params, returns)
        return ast.InterfaceDecl(loc, interface)

    def _parse_include(self) -> ast.IncludeDecl:
        """Parse include directive."""
        loc = self._location()
        self._expect(TokenType.INCLUDE, "Expected 'include'")
        path = self._expect(TokenType.STRING, "Expected include path").value
        self._match(TokenType.SEMICOLON)
        return ast.IncludeDecl(loc, path)

    def parse(self) -> ast.Program:
        """Parse the entire program."""
        loc = self._location()
        declarations = []
        statements = []

        while not self._at(TokenType.EOF):
            # Top-level declarations
            if self._at(TokenType.INCLUDE):
                declarations.append(self._parse_include())
            elif self._at(TokenType.SUB):
                declarations.append(self._parse_sub())
            elif self._at(TokenType.RECORD):
                declarations.append(self._parse_record())
            elif self._at(TokenType.TYPEDEF):
                declarations.append(self._parse_typedef())
            elif self._at(TokenType.INTERFACE):
                declarations.append(self._parse_interface())
            elif self._at(TokenType.AT):
                # @decl or @impl
                next_tok = self._peek_next()
                if next_tok.type == TokenType.ID:
                    if next_tok.value == 'decl':
                        self._advance()  # @
                        self._advance()  # decl
                        declarations.append(self._parse_sub(is_decl=True))
                    elif next_tok.value == 'impl':
                        self._advance()  # @
                        self._advance()  # impl
                        declarations.append(self._parse_sub(is_impl=True))
                    else:
                        # Top-level statement starting with @
                        statements.append(self._parse_statement())
                else:
                    statements.append(self._parse_statement())
            else:
                # Top-level statement
                statements.append(self._parse_statement())

        return ast.Program(loc, declarations, statements)


def parse_file(filename: str) -> ast.Program:
    """Parse a file and return the AST."""
    with open(filename, 'r') as f:
        source = f.read()
    lexer = Lexer(source, filename)
    parser = Parser(lexer)
    return parser.parse()


def parse_string(source: str, filename: str = "<input>") -> ast.Program:
    """Parse a string and return the AST."""
    lexer = Lexer(source, filename)
    parser = Parser(lexer)
    return parser.parse()
