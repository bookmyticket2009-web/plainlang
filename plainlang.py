#!/usr/bin/env python3
import ast
import builtins
import datetime
import inspect
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
import urllib.request


BUILTIN_FUNCTIONS = [
    "abs",
    "aiter",
    "all",
    "anext",
    "any",
    "ascii",
    "bin",
    "bool",
    "breakpoint",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "compile",
    "complex",
    "delattr",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "eval",
    "exec",
    "filter",
    "float",
    "format",
    "frozendict",
    "frozenset",
    "getattr",
    "globals",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "locals",
    "map",
    "max",
    "memoryview",
    "min",
    "next",
    "object",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "sentinel",
    "set",
    "setattr",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
    "__import__",
]


def _build_builtin_registry():
    registry = {}
    for name in BUILTIN_FUNCTIONS:
        if hasattr(builtins, name):
            registry[name] = getattr(builtins, name)
    if "frozendict" not in registry:
        registry["frozendict"] = lambda d=None: frozenset(dict(d or {}).items())
    if "sentinel" not in registry:
        registry["sentinel"] = lambda label="SENTINEL": object()
    return registry


PLAINLANG_BUILTINS = _build_builtin_registry()


class PlainLangReturn(Exception):
    def __init__(self, value):
        self.value = value


class PlainLangBreak(Exception):
    pass


class PlainLangContinue(Exception):
    pass


class PlainLangRuntimeError(Exception):
    pass


class PlainLangEngine:
    BLOCK_OPENERS = ("if ", "while ", "repeat ", "define ", "for each ", "try")

    def __init__(self, output_callback=None, input_callback=None, popup_callback=None):
        self.lines = []
        self.globals = {}
        self.functions = {}
        self.call_stack = []
        self.block_stack = []
        self.line_num = 0
        self.running = False
        self.output_callback = output_callback or print
        self.input_callback = input_callback or (lambda q: input(q + " "))
        self.popup_callback = popup_callback or self._default_popup

    @property
    def variables(self):
        merged = dict(self.globals)
        if self.call_stack:
            merged.update(self.call_stack[-1]["locals"])
        return merged

    def _default_popup(self, msg):
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("PlainLang", msg)
            root.destroy()
        except Exception:
            self.emit(f"[Popup] {msg}")

    def emit(self, text):
        self.output_callback(str(text))

    def reset(self, lines):
        self.lines = [line.strip() for line in lines]
        self.globals = {"last_result": None, "error": ""}
        self.functions = {}
        self.call_stack = []
        self.block_stack = []
        self.line_num = 0
        self.running = True

    def stop(self):
        self.running = False

    def _lookup_symbol(self, name):
        if self.call_stack and name in self.call_stack[-1]["locals"]:
            return True, self.call_stack[-1]["locals"][name]
        if name in self.globals:
            return True, self.globals[name]
        return False, None

    def _write_symbol(self, name, value):
        if self.call_stack and name in self.call_stack[-1]["locals"]:
            self.call_stack[-1]["locals"][name] = value
        else:
            self.globals[name] = value

    def get_var(self, name, default=None):
        name = name.strip()
        if "." in name:
            parts = name.split(".")
            found, curr = self._lookup_symbol(parts[0])
            if not found:
                return default
            for p in parts[1:]:
                if isinstance(curr, dict) and p in curr:
                    curr = curr[p]
                elif isinstance(curr, list) and p.lstrip("-").isdigit():
                    idx = int(p)
                    curr = curr[idx] if -len(curr) <= idx < len(curr) else default
                else:
                    return default
            return curr
        found, val = self._lookup_symbol(name)
        return val if found else default

    def set_var(self, name, value):
        name = name.strip()
        if "." in name:
            parts = name.split(".")
            found, root = self._lookup_symbol(parts[0])
            if not found or not isinstance(root, dict):
                root = {}
                self._write_symbol(parts[0], root)
            curr = root
            for p in parts[1:-1]:
                if not isinstance(curr.get(p), dict):
                    curr[p] = {}
                curr = curr[p]
            curr[parts[-1]] = value
        else:
            self._write_symbol(name, value)

    def resolve(self, text):
        text = str(text).strip()
        if not text:
            return ""

        tokens = text.split()
        out = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.lower() == "literal" and i + 1 < len(tokens):
                out.append(tokens[i + 1])
                i += 2
                continue

            val = self.get_var(tok, None)
            if val is not None:
                if isinstance(val, list):
                    out.append(", ".join(map(str, val)))
                elif isinstance(val, dict):
                    out.append(json.dumps(val))
                else:
                    out.append(str(val))
            else:
                out.append(tok)
            i += 1
        return " ".join(out)

    def _to_number(self, raw_val):
        if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
            return raw_val
        s = str(raw_val).strip()
        try:
            return float(s) if "." in s else int(s)
        except ValueError:
            raise PlainLangRuntimeError(f"Expected a number, got '{raw_val}'")

    def _safe_arith(self, expr):
        tree = ast.parse(expr, mode="eval")
        allowed_ops = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
            ast.UAdd, ast.USub,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed_ops):
                raise ValueError("Disallowed expression node")
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise ValueError("Non-numeric constant")
        return eval(compile(tree, "<noquote-expr>", "eval"), {"__builtins__": {}}, {})

    def _eval_value(self, expr_str):
        s = str(expr_str).strip()
        if not s:
            return ""

        low = s.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low in ("none", "null", "nothing"):
            return None

        if low.startswith("literal "):
            return s[8:].strip()

        if " " not in s:
            val = self.get_var(s, None)
            if val is not None:
                return val

        if re.fullmatch(r"[0-9+\-*/().%\s]+", s):
            try:
                return self._safe_arith(s)
            except Exception:
                pass

        return s

    def _eval_arg(self, raw_expr):
        raw_expr = str(raw_expr).strip()
        if not raw_expr:
            return ""
        if " " not in raw_expr:
            found, val = self._lookup_symbol(raw_expr)
            if found:
                return val
            if "." in raw_expr:
                dot_val = self.get_var(raw_expr, None)
                if dot_val is not None:
                    return dot_val
        return self._eval_value(self.resolve(raw_expr))

    def _eval_args_list(self, arg_text):
        arg_text = str(arg_text).strip()
        if not arg_text:
            return []
        if "," in arg_text:
            return [self._eval_arg(part) for part in arg_text.split(",")]
        return [self._eval_arg(arg_text)]

    def _resolve_builtin(self, name):
        key = str(name).strip()
        if key in PLAINLANG_BUILTINS:
            return PLAINLANG_BUILTINS[key]
        low = key.lower()
        if low in PLAINLANG_BUILTINS:
            return PLAINLANG_BUILTINS[low]
        raise PlainLangRuntimeError(f"Unknown built-in function '{name}'")

    def _invoke_builtin(self, name, args, target_var=None):
        fn = self._resolve_builtin(name)
        result = fn(*args)
        self.set_var("last_result", result)
        if target_var:
            self.set_var(target_var.strip(), result)
        return result

    def _eval_condition(self, cond_str):
        tokens = str(cond_str).strip().split()
        rebuilt = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            low = tok.lower()

            if low == "and":
                rebuilt.append(" and ")
                i += 1
            elif low == "or":
                rebuilt.append(" or ")
                i += 1
            elif low == "not" and i + 1 < len(tokens) and tokens[i + 1].lower() == "contains":
                rebuilt.append(" not in ")
                i += 2
            elif low == "not":
                rebuilt.append(" not ")
                i += 1
            elif low == "contains":
                rebuilt.append(" in ")
                i += 1
            elif low == "equals":
                rebuilt.append(" == ")
                i += 1
            elif low == "is" and i + 2 < len(tokens) and tokens[i + 1].lower() == "greater" and tokens[i + 2].lower() == "than":
                rebuilt.append(" > ")
                i += 3
            elif low == "is" and i + 2 < len(tokens) and tokens[i + 1].lower() == "less" and tokens[i + 2].lower() == "than":
                rebuilt.append(" < ")
                i += 3
            elif low == "is" and i + 3 < len(tokens) and tokens[i + 1].lower() == "at" and tokens[i + 2].lower() == "least":
                rebuilt.append(" >= ")
                i += 4
            elif low == "is" and i + 3 < len(tokens) and tokens[i + 1].lower() == "at" and tokens[i + 2].lower() == "most":
                rebuilt.append(" <= ")
                i += 4
            elif low == "is" and i + 2 < len(tokens) and tokens[i + 1].lower() == "equal" and tokens[i + 2].lower() == "to":
                rebuilt.append(" == ")
                i += 3
            elif low == "true":
                rebuilt.append("True")
                i += 1
            elif low == "false":
                rebuilt.append("False")
                i += 1
            else:
                val = self.get_var(tok, None)
                if val is not None:
                    rebuilt.append(repr(val))
                else:
                    try:
                        float(tok)
                        rebuilt.append(tok)
                    except ValueError:
                        rebuilt.append(repr(tok))
                i += 1

        expr = "".join(rebuilt)
        return bool(eval(expr, {"__builtins__": {}}, {}))

    def find_matching_end(self, start_idx):
        depth = 1
        for i in range(start_idx + 1, len(self.lines)):
            cmd = self.lines[i].lower()
            if cmd.startswith(self.BLOCK_OPENERS):
                depth += 1
            elif cmd == "end":
                depth -= 1
                if depth == 0:
                    return i
        return len(self.lines)

    def find_if_branch(self, start_idx):
        depth = 1
        for i in range(start_idx + 1, len(self.lines)):
            cmd = self.lines[i].lower()
            if cmd.startswith(self.BLOCK_OPENERS):
                depth += 1
            elif cmd == "end":
                depth -= 1
                if depth == 0:
                    return i, "end"
            elif depth == 1:
                if cmd.startswith(("else if ", "otherwise if ")):
                    return i, "elif"
                if cmd in ("else", "otherwise"):
                    return i, "else"
        return len(self.lines), "end"

    def find_try_branch(self, start_idx):
        depth = 1
        for i in range(start_idx + 1, len(self.lines)):
            cmd = self.lines[i].lower()
            if cmd.startswith(self.BLOCK_OPENERS):
                depth += 1
            elif cmd == "end":
                depth -= 1
                if depth == 0:
                    return i, "end"
            elif depth == 1 and (cmd == "catch" or cmd == "except" or cmd.startswith(("catch ", "except "))):
                return i, "catch"
        return len(self.lines), "end"

    def _split_ci(self, text, sep):
        idx = text.lower().find(sep.lower())
        if idx == -1:
            return None
        return text[:idx], text[idx + len(sep):]

    def _pop_function_frame(self):
        while self.block_stack and self.block_stack[-1]["type"] != "function":
            self.block_stack.pop()
        if self.block_stack:
            self.block_stack.pop()
        if self.call_stack:
            frame = self.call_stack.pop()
            self.globals.update(frame["saved_globals"])
            return frame["return_line"]
        return None

    def execute_line(self, line):
        lower = line.lower()

        if not line or line.startswith("#"):
            self.line_num += 1
            return True

        if lower in ("break", "stop loop"):
            raise PlainLangBreak()
        if lower in ("continue", "skip iteration"):
            raise PlainLangContinue()
        if lower == "breakpoint":
            self._invoke_builtin("breakpoint", [])
        if lower.startswith("return "):
            ret_val = self._eval_value(self.resolve(line[7:].strip()))
            raise PlainLangReturn(ret_val)

        if lower.startswith("define "):
            raw = line[7:].strip()
            split_fn = self._split_ci(raw, " with ")
            if split_fn:
                func_name = split_fn[0].strip()
                params = [p.strip() for p in split_fn[1].replace(",", " ").split() if p.strip()]
            else:
                func_name = raw
                params = []
            self.functions[func_name] = {"start": self.line_num, "params": params}
            self.line_num = self.find_matching_end(self.line_num)

        elif lower.startswith("do "):
            raw = line[3:].strip()
            split_call = self._split_ci(raw, " with ")
            if split_call:
                func_name = split_call[0].strip()
                arg_exprs = [a.strip() for a in split_call[1].split(",")]
            else:
                func_name = raw
                arg_exprs = []

            if func_name not in self.functions:
                raise PlainLangRuntimeError(f"Function '{func_name}' not found.")

            fn = self.functions[func_name]
            locals_map = {}
            saved_globals = {}
            for idx_p, param_name in enumerate(fn["params"]):
                if param_name in self.globals:
                    saved_globals[param_name] = self.globals[param_name]
                if idx_p < len(arg_exprs):
                    locals_map[param_name] = self._eval_value(self.resolve(arg_exprs[idx_p]))
                else:
                    locals_map[param_name] = None

            self.call_stack.append({
                "return_line": self.line_num,
                "locals": locals_map,
                "saved_globals": saved_globals,
            })
            self.block_stack.append({"type": "function"})
            self.line_num = fn["start"]

        # --- PYTHON BUILT-INS IN PLAIN LANGUAGE ---
        elif lower.startswith("call "):
            rest = line[5:].strip()
            with_split = self._split_ci(rest, " with ")
            if with_split:
                fn_name = with_split[0].strip()
                into_split = self._split_ci(with_split[1], " into ")
                if into_split:
                    args = self._eval_args_list(into_split[0])
                    target_var = into_split[1].strip()
                else:
                    args = self._eval_args_list(with_split[1])
                    target_var = None
            else:
                into_split = self._split_ci(rest, " into ")
                if into_split:
                    fn_name = into_split[0].strip()
                    args = []
                    target_var = into_split[1].strip()
                else:
                    fn_name = rest
                    args = []
                    target_var = None
            self._invoke_builtin(fn_name, args, target_var)

        elif lower.startswith("absolute value of "):
            parts = self._split_ci(line[18:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'absolute value of' syntax.")
            val = self._to_number(self._eval_arg(parts[0]))
            self._invoke_builtin("abs", [val], parts[1])

        elif lower.startswith("round "):
            parts = self._split_ci(line[6:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'round' syntax.")
            raw_left = parts[0].strip()
            digits_split = self._split_ci(raw_left, " to ")
            if digits_split:
                num = self._to_number(self._eval_arg(digits_split[0]))
                ndigits = int(self._to_number(self._eval_arg(digits_split[1])))
                self._invoke_builtin("round", [num, ndigits], parts[1])
            else:
                num = self._to_number(self._eval_arg(raw_left))
                self._invoke_builtin("round", [num], parts[1])

        elif lower.startswith("power "):
            s1 = self._split_ci(line[6:], " to ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'power' syntax.")
            base = self._to_number(self._eval_arg(s1[0]))
            exp_val = self._to_number(self._eval_arg(s2[0]))
            self._invoke_builtin("pow", [base, exp_val], s2[1])

        elif lower.startswith("divmod "):
            s1 = self._split_ci(line[7:], " by ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'divmod' syntax.")
            a = self._to_number(self._eval_arg(s1[0]))
            b = self._to_number(self._eval_arg(s2[0]))
            self._invoke_builtin("divmod", [a, b], s2[1])

        elif lower.startswith("maximum of "):
            parts = self._split_ci(line[11:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'maximum of' syntax.")
            args = self._eval_args_list(parts[0])
            self._invoke_builtin("max", args, parts[1])

        elif lower.startswith("minimum of "):
            parts = self._split_ci(line[11:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'minimum of' syntax.")
            args = self._eval_args_list(parts[0])
            self._invoke_builtin("min", args, parts[1])

        elif lower.startswith("sum of "):
            parts = self._split_ci(line[7:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'sum of' syntax.")
            args = self._eval_args_list(parts[0])
            self._invoke_builtin("sum", args, parts[1])

        elif lower.startswith("range from "):
            s1 = self._split_ci(line[11:], " to ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'range from' syntax.")
            start_v = int(self._to_number(self._eval_arg(s1[0])))
            end_part = s2[0].strip()
            step_split = self._split_ci(end_part, " step ")
            if step_split:
                stop_v = int(self._to_number(self._eval_arg(step_split[0])))
                step_v = int(self._to_number(self._eval_arg(step_split[1])))
                rng = self._invoke_builtin("range", [start_v, stop_v, step_v], None)
            else:
                stop_v = int(self._to_number(self._eval_arg(end_part)))
                rng = self._invoke_builtin("range", [start_v, stop_v], None)
            self.set_var(s2[1].strip(), list(rng))

        elif lower.startswith("convert "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'convert' syntax.")
            target_var = parts[1].strip()
            as_split = self._split_ci(parts[0], " to ")
            if not as_split:
                raise PlainLangRuntimeError("Invalid 'convert ... to ... into ...' syntax.")
            val = self._eval_arg(as_split[0])
            kind = as_split[1].strip().lower()
            type_aliases = {
                "int": "int",
                "integer": "int",
                "float": "float",
                "decimal": "float",
                "str": "str",
                "string": "str",
                "text": "str",
                "bool": "bool",
                "boolean": "bool",
                "list": "list",
                "tuple": "tuple",
                "set": "set",
                "frozenset": "frozenset",
                "dict": "dict",
                "dictionary": "dict",
                "object": "dict",
                "frozendict": "frozendict",
                "bytes": "bytes",
                "bytearray": "bytearray",
                "complex": "complex",
                "hex": "hex",
                "oct": "oct",
                "bin": "bin",
                "ascii": "ascii",
                "repr": "repr",
            }
            mapped = type_aliases.get(kind, kind)
            if mapped == "dict" and isinstance(val, str):
                parsed = json.loads(val)
                self.set_var("last_result", parsed)
                self.set_var(target_var, parsed)
            elif mapped == "int" and isinstance(val, str):
                self._invoke_builtin("int", [int(float(val.strip()))], target_var)
            else:
                self._invoke_builtin(mapped, [val], target_var)

        elif lower.startswith("character of "):
            parts = self._split_ci(line[13:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'character of' syntax.")
            code = int(self._to_number(self._eval_arg(parts[0])))
            self._invoke_builtin("chr", [code], parts[1])

        elif lower.startswith("unicode of "):
            parts = self._split_ci(line[11:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'unicode of' syntax.")
            ch = str(self._eval_arg(parts[0]))
            self._invoke_builtin("ord", [ch], parts[1])

        elif lower.startswith("type of "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'type of' syntax.")
            val = self._eval_arg(parts[0])
            t = self._invoke_builtin("type", [val], None)
            self.set_var(parts[1].strip(), getattr(t, "__name__", str(t)))

        elif lower.startswith("hash of "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'hash of' syntax.")
            val = self._eval_arg(parts[0])
            self._invoke_builtin("hash", [val], parts[1])

        elif lower.startswith("id of "):
            parts = self._split_ci(line[6:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'id of' syntax.")
            val = self._eval_arg(parts[0])
            self._invoke_builtin("id", [val], parts[1])

        elif lower.startswith("check all "):
            parts = self._split_ci(line[10:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'check all' syntax.")
            items = self._eval_args_list(parts[0])
            seq = items[0] if len(items) == 1 and isinstance(items[0], (list, tuple, set, frozenset)) else items
            self._invoke_builtin("all", [seq], parts[1])

        elif lower.startswith("check any "):
            parts = self._split_ci(line[10:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'check any' syntax.")
            items = self._eval_args_list(parts[0])
            seq = items[0] if len(items) == 1 and isinstance(items[0], (list, tuple, set, frozenset)) else items
            self._invoke_builtin("any", [seq], parts[1])

        elif lower.startswith("enumerate "):
            parts = self._split_ci(line[10:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'enumerate' syntax.")
            raw_left = parts[0].strip()
            start_split = self._split_ci(raw_left, " start ")
            if start_split:
                seq = self._eval_arg(start_split[0])
                start_idx = int(self._to_number(self._eval_arg(start_split[1])))
                enum_obj = self._invoke_builtin("enumerate", [seq, start_idx], None)
            else:
                seq = self._eval_arg(raw_left)
                enum_obj = self._invoke_builtin("enumerate", [seq], None)
            formatted = [{"index": idx, "value": item} for idx, item in enum_obj]
            self.set_var(parts[1].strip(), formatted)

        elif lower.startswith("zip "):
            parts = self._split_ci(line[4:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'zip' syntax.")
            seqs = [self._eval_arg(s) for s in parts[0].split(" and ")]
            zipped = [list(row) for row in self._invoke_builtin("zip", seqs, None)]
            self.set_var(parts[1].strip(), zipped)

        elif lower.startswith("reverse "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'reverse' syntax.")
            seq = self._eval_arg(parts[0])
            rev = list(self._invoke_builtin("reversed", [seq], None))
            self.set_var(parts[1].strip(), rev)

        elif lower.startswith("sort "):
            parts = self._split_ci(line[5:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'sort' syntax.")
            seq = self._eval_arg(parts[0])
            sorted_items = self._invoke_builtin("sorted", [seq], None)
            self.set_var(parts[1].strip(), sorted_items)

        elif lower.startswith("slice "):
            parts = self._split_ci(line[6:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'slice' syntax.")
            target_var = parts[1].strip()
            seq_split = self._split_ci(parts[0], " from ")
            seq = self._eval_arg(seq_split[0]) if seq_split else self._eval_arg(parts[0])
            start_i = None
            stop_i = None
            step_i = None
            if seq_split:
                rem = seq_split[1]
                step_split = self._split_ci(rem, " step ")
                if step_split:
                    step_i = int(self._to_number(self._eval_arg(step_split[1])))
                    rem = step_split[0]
                to_split = self._split_ci(rem, " to ")
                if to_split:
                    start_i = int(self._to_number(self._eval_arg(to_split[0])))
                    stop_i = int(self._to_number(self._eval_arg(to_split[1])))
                elif rem.strip():
                    stop_i = int(self._to_number(self._eval_arg(rem)))
            sl = self._invoke_builtin("slice", [start_i, stop_i, step_i], None)
            self.set_var(target_var, seq[sl])

        elif lower.startswith("has attribute "):
            s1 = self._split_ci(line[14:], " of ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'has attribute' syntax.")
            attr_name = str(self._eval_arg(s1[0]))
            obj = self._eval_arg(s2[0])
            self._invoke_builtin("hasattr", [obj, attr_name], s2[1])

        elif lower.startswith("get attribute "):
            s1 = self._split_ci(line[14:], " of ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'get attribute' syntax.")
            attr_name = str(self._eval_arg(s1[0]))
            obj = self._eval_arg(s2[0])
            self._invoke_builtin("getattr", [obj, attr_name], s2[1])

        elif lower.startswith("set attribute "):
            s1 = self._split_ci(line[14:], " of ")
            s2 = self._split_ci(s1[1], " to ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'set attribute' syntax.")
            attr_name = str(self._eval_arg(s1[0]))
            into_split = self._split_ci(s2[1], " into ")
            if into_split:
                obj = self._eval_arg(s2[0])
                val = self._eval_arg(into_split[0])
                self._invoke_builtin("setattr", [obj, attr_name, val], into_split[1])
            else:
                obj = self._eval_arg(s2[0])
                val = self._eval_arg(s2[1])
                self._invoke_builtin("setattr", [obj, attr_name, val], None)

        elif lower.startswith("delete attribute "):
            parts = self._split_ci(line[17:], " of ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'delete attribute' syntax.")
            attr_name = str(self._eval_arg(parts[0]))
            obj = self._eval_arg(parts[1])
            self._invoke_builtin("delattr", [obj, attr_name], None)

        elif lower.startswith("check callable "):
            parts = self._split_ci(line[15:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'check callable' syntax.")
            val = self._eval_arg(parts[0])
            self._invoke_builtin("callable", [val], parts[1])

        elif lower.startswith("check instance of "):
            s1 = self._split_ci(line[18:], " as ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'check instance of' syntax.")
            val = self._eval_arg(s1[0])
            cls_val = self._eval_arg(s2[0])
            self._invoke_builtin("isinstance", [val, cls_val], s2[1])

        elif lower.startswith("check subclass of "):
            s1 = self._split_ci(line[18:], " as ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'check subclass of' syntax.")
            sub_val = self._eval_arg(s1[0])
            sup_val = self._eval_arg(s2[0])
            self._invoke_builtin("issubclass", [sub_val, sup_val], s2[1])

        elif lower.startswith("iterate "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'iterate' syntax.")
            seq = self._eval_arg(parts[0])
            self._invoke_builtin("iter", [seq], parts[1])

        elif lower.startswith("next item of "):
            parts = self._split_ci(line[13:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'next item of' syntax.")
            it = self._eval_arg(parts[0])
            self._invoke_builtin("next", [it], parts[1])

        elif lower.startswith("format "):
            s1 = self._split_ci(line[7:], " with ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'format' syntax.")
            val = self._eval_arg(s1[0])
            fmt_spec = self.resolve(s2[0].strip())
            self._invoke_builtin("format", [val, fmt_spec], s2[1])

        elif lower.startswith("open file "):
            s1 = self._split_ci(line[10:], " mode ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'open file' syntax.")
            fpath = self.resolve(s1[0].strip())
            mode = self.resolve(s2[0].strip())
            self._invoke_builtin("open", [fpath, mode], s2[1])

        elif lower.startswith("help on "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'help on' syntax.")
            obj = self._eval_arg(parts[0])
            self._invoke_builtin("help", [obj], None)
            self.set_var(parts[1].strip(), getattr(obj, "__doc__", "") or "")

        elif lower.startswith("eval python "):
            parts = self._split_ci(line[12:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'eval python' syntax.")
            code_expr = self.resolve(parts[0].strip())
            compiled = self._invoke_builtin("compile", [code_expr, "<plainlang-eval>", "eval"], None)
            ns = dict(self.variables)
            ns.update(PLAINLANG_BUILTINS)
            res = self._invoke_builtin("eval", [compiled, {"__builtins__": PLAINLANG_BUILTINS}, ns], None)
            self.set_var(parts[1].strip(), res)

        elif lower.startswith("run python "):
            parts = self._split_ci(line[11:], " into ")
            code_str = self.resolve(parts[0].strip()) if parts else self.resolve(line[11:].strip())
            target_var = parts[1].strip() if parts else None
            compiled = self._invoke_builtin("compile", [code_str, "<plainlang-exec>", "exec"], None)
            ns = dict(self.variables)
            ns.update(PLAINLANG_BUILTINS)
            self._invoke_builtin("exec", [compiled, {"__builtins__": PLAINLANG_BUILTINS}, ns], None)
            for k, v in ns.items():
                if k not in PLAINLANG_BUILTINS and not k.startswith("__"):
                    self.set_var(k, v)
            if target_var:
                self.set_var(target_var, ns.get("result", self.get_var("last_result")))

        elif lower.startswith("filter "):
            s1 = self._split_ci(line[7:], " with ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'filter' syntax.")
            seq = self._eval_arg(s1[0])
            pred = self._eval_arg(s2[0])
            res = list(self._invoke_builtin("filter", [pred, seq], None))
            self.set_var(s2[1].strip(), res)

        elif lower.startswith("map "):
            s1 = self._split_ci(line[4:], " with ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'map' syntax.")
            seq = self._eval_arg(s1[0])
            fn_obj = self._eval_arg(s2[0])
            res = list(self._invoke_builtin("map", [fn_obj, seq], None))
            self.set_var(s2[1].strip(), res)

        elif lower.startswith("import module "):
            parts = self._split_ci(line[14:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'import module' syntax.")
            mod_name = self.resolve(parts[0].strip())
            mod = self._invoke_builtin("__import__", [mod_name], None)
            self.set_var(parts[1].strip(), mod)

        elif lower.startswith("globals into "):
            self.set_var(line[13:].strip(), self._invoke_builtin("globals", [], None))

        elif lower.startswith("locals into "):
            self.set_var(line[12:].strip(), self._invoke_builtin("locals", [], None))

        elif lower.startswith("dir of "):
            parts = self._split_ci(line[7:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'dir of' syntax.")
            obj = self._eval_arg(parts[0])
            self._invoke_builtin("dir", [obj], parts[1])

        elif lower.startswith("vars of "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'vars of' syntax.")
            obj = self._eval_arg(parts[0])
            self._invoke_builtin("vars", [obj], parts[1])

        elif lower.startswith("memoryview of "):
            parts = self._split_ci(line[14:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'memoryview of' syntax.")
            obj = self._eval_arg(parts[0])
            self._invoke_builtin("memoryview", [obj], parts[1])

        elif lower.startswith("object into "):
            self.set_var(line[12:].strip(), self._invoke_builtin("object", [], None))

        elif lower.startswith("super into "):
            self.set_var(line[11:].strip(), self._invoke_builtin("super", [], None))

        elif lower.startswith("property "):
            parts = self._split_ci(line[9:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'property' syntax.")
            args = self._eval_args_list(parts[0])
            self._invoke_builtin("property", args, parts[1])

        elif lower.startswith("classmethod "):
            parts = self._split_ci(line[12:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'classmethod' syntax.")
            fn_obj = self._eval_arg(parts[0])
            self._invoke_builtin("classmethod", [fn_obj], parts[1])

        elif lower.startswith("staticmethod "):
            parts = self._split_ci(line[13:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'staticmethod' syntax.")
            fn_obj = self._eval_arg(parts[0])
            self._invoke_builtin("staticmethod", [fn_obj], parts[1])

        # --- TRY / CATCH ---
        elif lower == "try":
            self.block_stack.append({"type": "try"})

        elif lower == "catch" or lower == "except" or lower.startswith(("catch ", "except ")):
            if self.block_stack and self.block_stack[-1]["type"] == "try":
                self.block_stack.pop()
            self.line_num = self.find_matching_end(self.line_num)

        # --- OBJECTS & JSON ---
        elif lower.startswith("create object "):
            obj_name = line[14:].strip()
            self.set_var(obj_name, {})

        elif lower.startswith("set key "):
            s1 = self._split_ci(line[8:], " of ")
            s2 = self._split_ci(s1[1], " to ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'set key' syntax.")
            key_name = self.resolve(s1[0].strip())
            obj_name = s2[0].strip()
            val = self._eval_value(self.resolve(s2[1].strip()))
            obj = self.get_var(obj_name)
            if not isinstance(obj, dict):
                obj = {}
                self.set_var(obj_name, obj)
            obj[key_name] = val

        elif lower.startswith("parse json "):
            parts = self._split_ci(line[11:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'parse json' syntax.")
            raw_json = self.resolve(parts[0].strip())
            self.set_var(parts[1].strip(), json.loads(raw_json))

        elif lower.startswith("to json "):
            parts = self._split_ci(line[8:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'to json' syntax.")
            val = self.get_var(parts[0].strip(), self._eval_value(self.resolve(parts[0].strip())))
            self.set_var(parts[1].strip(), json.dumps(val, indent=2))

        # --- LISTS ---
        elif lower.startswith("create list "):
            self.set_var(line[12:].strip(), [])

        elif lower.startswith("append "):
            parts = self._split_ci(line[7:], " to ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'append' syntax.")
            item_val = self._eval_value(self.resolve(parts[0].strip()))
            target = self.get_var(parts[1].strip())
            if not isinstance(target, list):
                raise PlainLangRuntimeError(f"'{parts[1].strip()}' is not a list.")
            target.append(item_val)

        elif lower.startswith("pop from "):
            parts = self._split_ci(line[9:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'pop from' syntax.")
            target = self.get_var(parts[0].strip())
            if not isinstance(target, list) or not target:
                raise PlainLangRuntimeError("Cannot pop from empty or non-list.")
            self.set_var(parts[1].strip(), target.pop())

        elif lower.startswith("get item "):
            s1 = self._split_ci(line[9:], " of ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            if not s1 or not s2:
                raise PlainLangRuntimeError("Invalid 'get item' syntax.")
            idx = int(self._to_number(self.resolve(s1[0].strip())))
            target_list = self.get_var(s2[0].strip())
            if isinstance(target_list, list) and -len(target_list) <= idx < len(target_list):
                self.set_var(s2[1].strip(), target_list[idx])
            else:
                raise PlainLangRuntimeError(f"Invalid list index {idx}.")

        elif lower.startswith("length of "):
            parts = self._split_ci(line[10:], " into ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'length of' syntax.")
            target = self.get_var(parts[0].strip(), self._eval_value(self.resolve(parts[0].strip())))
            self._invoke_builtin("len", [target], parts[1])

        elif lower.startswith("sort list "):
            list_name = line[10:].strip()
            target = self.get_var(list_name)
            if not isinstance(target, list):
                raise PlainLangRuntimeError(f"'{list_name}' is not a list.")
            target.sort(key=lambda x: (str(type(x).__name__), str(x)))

        elif lower.startswith("reverse list "):
            list_name = line[13:].strip()
            target = self.get_var(list_name)
            if not isinstance(target, list):
                raise PlainLangRuntimeError(f"'{list_name}' is not a list.")
            target.reverse()

        elif lower.startswith("for each "):
            parts = self._split_ci(line[9:], " in ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'for each' syntax.")
            item_var = parts[0].strip()
            list_name = parts[1].strip()
            target_list = self.get_var(list_name)
            if not isinstance(target_list, list):
                raise PlainLangRuntimeError(f"'{list_name}' is not a list.")
            if len(target_list) > 0:
                self.set_var(item_var, target_list[0])
                self.block_stack.append({
                    "type": "foreach",
                    "start": self.line_num,
                    "var": item_var,
                    "list": target_list,
                    "index": 0,
                })
            else:
                self.line_num = self.find_matching_end(self.line_num)

        elif lower.startswith("remove "):
            parts = self._split_ci(line[7:], " from ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'remove' syntax.")
            item = self._eval_value(self.resolve(parts[0].strip()))
            target = self.get_var(parts[1].strip())
            if not isinstance(target, list):
                raise PlainLangRuntimeError(f"'{parts[1].strip()}' is not a list.")
            if item in target:
                target.remove(item)

        # --- STRING HELPERS ---
        elif lower.startswith("uppercase "):
            parts = self._split_ci(line[10:], " into ")
            self.set_var(parts[1].strip(), self.resolve(parts[0].strip()).upper())

        elif lower.startswith("lowercase "):
            parts = self._split_ci(line[10:], " into ")
            self.set_var(parts[1].strip(), self.resolve(parts[0].strip()).lower())

        elif lower.startswith("trim "):
            parts = self._split_ci(line[5:], " into ")
            self.set_var(parts[1].strip(), self.resolve(parts[0].strip()).strip())

        elif lower.startswith("split "):
            s1 = self._split_ci(line[6:], " by ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            txt = self.resolve(s1[0].strip())
            sep = self.resolve(s2[0].strip())
            parts_list = txt.split() if sep.lower() == "space" else txt.split(sep)
            self.set_var(s2[1].strip(), parts_list)

        elif lower.startswith("join "):
            s1 = self._split_ci(line[5:], " with ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            target_list = self.get_var(s1[0].strip(), [])
            sep = self.resolve(s2[0].strip())
            sep = " " if sep.lower() == "space" else sep
            joined = sep.join(map(str, target_list)) if isinstance(target_list, list) else str(target_list)
            self.set_var(s2[1].strip(), joined)

        elif lower.startswith("replace "):
            s1 = self._split_ci(line[8:], " with ")
            s2 = self._split_ci(s1[1], " in ") if s1 else None
            s3 = self._split_ci(s2[1], " into ") if s2 else None
            old_s = self.resolve(s1[0].strip())
            new_s = self.resolve(s2[0].strip())
            src_s = self.resolve(s3[0].strip())
            self.set_var(s3[1].strip(), src_s.replace(old_s, new_s))

        # --- FILE, WEB & SYSTEM ---
        elif lower.startswith("save "):
            parts = self._split_ci(line[5:], " to ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'save' syntax.")
            var_name = parts[0].strip()
            file_target = self.resolve(parts[1].strip())
            if not os.path.isabs(file_target):
                base_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.getcwd()
                file_target = os.path.join(base_dir, file_target)
            val = self.get_var(var_name, None)
            if val is None:
                raise PlainLangRuntimeError(f"Nothing named '{var_name}' to save.")
            try:
                with open(file_target, "w", encoding="utf-8") as out_f:
                    if isinstance(val, list):
                        out_f.write("\n".join(map(str, val)))
                    elif isinstance(val, dict):
                        out_f.write(json.dumps(val, indent=2))
                    else:
                        out_f.write(str(val))
            except PermissionError:
                raise PlainLangRuntimeError(
                    f"Permission denied writing '{file_target}'. Close the file if it is open or check permissions."
                )

        elif lower.startswith("load "):
            parts = self._split_ci(line[5:], " into ")
            file_source = self.resolve(parts[0].strip())
            var_name = parts[1].strip()
            if not os.path.exists(file_source):
                raise PlainLangRuntimeError(f"File '{file_source}' does not exist.")
            with open(file_source, "r", encoding="utf-8") as in_f:
                content = in_f.read()
            if file_source.lower().endswith(".json"):
                try:
                    self.set_var(var_name, json.loads(content))
                    self.line_num += 1
                    return True
                except json.JSONDecodeError:
                    pass
            file_lines = [l.strip() for l in content.splitlines() if l.strip()]
            self.set_var(
                var_name,
                file_lines if len(file_lines) > 1 else (file_lines[0] if file_lines else ""),
            )

        elif lower.startswith("check file "):
            parts = self._split_ci(line[11:], " and save to ")
            fpath = self.resolve(parts[0].strip())
            self.set_var(parts[1].strip(), os.path.exists(fpath))

        elif lower.startswith("fetch "):
            parts = self._split_ci(line[6:], " and save to ")
            url = self.resolve(parts[0].strip())
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                self.set_var(parts[1].strip(), json.loads(body))
            except json.JSONDecodeError:
                self.set_var(parts[1].strip(), body)

        elif lower.startswith("run command "):
            parts = self._split_ci(line[12:], " and save to ")
            cmd_str = self.resolve(parts[0].strip())
            proc = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, check=False)
            out_text = proc.stdout.strip() if proc.stdout.strip() else proc.stderr.strip()
            self.set_var(parts[1].strip(), out_text)

        elif lower.startswith("get current time into "):
            self.set_var(line[22:].strip(), datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        elif lower.startswith("wait ") and lower.endswith(" seconds"):
            secs = float(self._to_number(self.resolve(lower[5:-8].strip())))
            end_time = time.time() + max(0.0, secs)
            while self.running and time.time() < end_time:
                time.sleep(0.05)

        elif lower.startswith("print "):
            self.emit(self.resolve(line[6:]))

        elif lower.startswith("show popup "):
            self.popup_callback(self.resolve(line[11:]))

        elif lower.startswith("set "):
            parts = self._split_ci(line[4:], " to ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'set' syntax.")
            var_name = parts[0].strip()
            val_expr = self.resolve(parts[1].strip())
            self.set_var(var_name, self._eval_value(val_expr))

        elif lower.startswith("add "):
            parts = self._split_ci(line[4:], " to ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'add' syntax.")
            raw_val = self.resolve(parts[0].strip())
            target_name = parts[1].strip()
            current = self.get_var(target_name, None)
            if isinstance(current, list):
                current.append(self._eval_value(raw_val))
            else:
                num = self._to_number(raw_val)
                base = current if isinstance(current, (int, float)) and not isinstance(current, bool) else 0
                self.set_var(target_name, base + num)

        elif lower.startswith("subtract "):
            parts = self._split_ci(line[9:], " from ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'subtract' syntax.")
            num = self._to_number(self.resolve(parts[0].strip()))
            current = self.get_var(parts[1].strip(), 0)
            base = current if isinstance(current, (int, float)) and not isinstance(current, bool) else 0
            self.set_var(parts[1].strip(), base - num)

        elif lower.startswith("multiply "):
            s1 = self._split_ci(line[9:], " by ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            a_num = self._to_number(self.resolve(s1[0].strip()))
            b_num = self._to_number(self.resolve(s2[0].strip()))
            self.set_var(s2[1].strip(), a_num * b_num)

        elif lower.startswith("divide "):
            s1 = self._split_ci(line[7:], " by ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            a_num = self._to_number(self.resolve(s1[0].strip()))
            b_num = self._to_number(self.resolve(s2[0].strip()))
            if b_num == 0:
                raise ZeroDivisionError("Cannot divide by zero")
            self.set_var(s2[1].strip(), a_num / b_num)

        elif lower.startswith("modulo "):
            s1 = self._split_ci(line[7:], " by ")
            s2 = self._split_ci(s1[1], " into ") if s1 else None
            a_num = self._to_number(self.resolve(s1[0].strip()))
            b_num = self._to_number(self.resolve(s2[0].strip()))
            if b_num == 0:
                raise ZeroDivisionError("Cannot modulo by zero")
            self.set_var(s2[1].strip(), a_num % b_num)

        elif lower.startswith("pick random between "):
            s1 = self._split_ci(line[20:], " and save to ")
            bounds = self._split_ci(s1[0], " and ") if s1 else None
            if not s1 or not bounds:
                raise PlainLangRuntimeError("Invalid 'pick random between' syntax.")
            low = int(self._to_number(self.resolve(bounds[0].strip())))
            high = int(self._to_number(self.resolve(bounds[1].strip())))
            self.set_var(s1[1].strip(), random.randint(low, high))

        elif lower.startswith("ask "):
            parts = self._split_ci(line[4:], " and save to ")
            if not parts:
                raise PlainLangRuntimeError("Invalid 'ask' syntax.")
            question = self.resolve(parts[0].strip())
            ans = self.input_callback(question)
            if ans is None:
                return False
            ans = str(ans).strip()
            self.set_var(parts[1].strip(), int(ans) if ans.isdigit() else ans)

        elif lower.startswith("if "):
            condition = line[3:].strip()
            if self._eval_condition(condition):
                self.block_stack.append({"type": "if", "matched": True})
            else:
                self.block_stack.append({"type": "if", "matched": False})
                target_idx, kind = self.find_if_branch(self.line_num)
                if kind == "elif":
                    self.line_num = target_idx - 1
                elif kind == "else":
                    self.block_stack[-1]["type"] = "else"
                    self.block_stack[-1]["matched"] = True
                    self.line_num = target_idx
                else:
                    self.block_stack.pop()
                    self.line_num = target_idx

        elif lower.startswith(("else if ", "otherwise if ")):
            prefix_len = 8 if lower.startswith("else if ") else 13
            cond_str = line[prefix_len:].strip()
            if self.block_stack and self.block_stack[-1]["type"] in ("if", "elif") and self.block_stack[-1]["matched"]:
                self.block_stack.pop()
                self.line_num = self.find_matching_end(self.line_num)
            else:
                if not self.block_stack or self.block_stack[-1]["type"] not in ("if", "elif"):
                    self.block_stack.append({"type": "elif", "matched": False})
                if self._eval_condition(cond_str):
                    self.block_stack[-1]["type"] = "elif"
                    self.block_stack[-1]["matched"] = True
                else:
                    target_idx, kind = self.find_if_branch(self.line_num)
                    if kind == "elif":
                        self.line_num = target_idx - 1
                    elif kind == "else":
                        self.block_stack[-1]["type"] = "else"
                        self.block_stack[-1]["matched"] = True
                        self.line_num = target_idx
                    else:
                        self.block_stack.pop()
                        self.line_num = target_idx

        elif lower in ("else", "otherwise"):
            if self.block_stack and self.block_stack[-1]["type"] in ("if", "elif") and self.block_stack[-1]["matched"]:
                self.block_stack.pop()
                self.line_num = self.find_matching_end(self.line_num)
            else:
                if not self.block_stack or self.block_stack[-1]["type"] not in ("if", "elif"):
                    self.block_stack.append({"type": "else", "matched": True})
                else:
                    self.block_stack[-1]["type"] = "else"
                    self.block_stack[-1]["matched"] = True

        elif lower.startswith("while "):
            cond_str = line[6:].strip()
            if self._eval_condition(cond_str):
                self.block_stack.append({"type": "while", "start": self.line_num, "cond": cond_str})
            else:
                self.line_num = self.find_matching_end(self.line_num)

        elif lower.startswith("repeat ") and lower.endswith(" times"):
            count = int(self._to_number(self.resolve(lower[7:-6].strip())))
            if count > 0:
                self.block_stack.append({
                    "type": "repeat",
                    "start": self.line_num,
                    "remaining": count - 1,
                })
            else:
                self.line_num = self.find_matching_end(self.line_num)

        elif lower == "end":
            if not self.block_stack:
                raise PlainLangRuntimeError("Unexpected 'End'")

            current_block = self.block_stack[-1]

            if current_block["type"] == "repeat":
                if current_block["remaining"] > 0:
                    current_block["remaining"] -= 1
                    self.line_num = current_block["start"]
                else:
                    self.block_stack.pop()

            elif current_block["type"] == "while":
                if self._eval_condition(current_block["cond"]):
                    self.line_num = current_block["start"]
                else:
                    self.block_stack.pop()

            elif current_block["type"] == "foreach":
                next_idx = current_block["index"] + 1
                if next_idx < len(current_block["list"]):
                    current_block["index"] = next_idx
                    self.set_var(current_block["var"], current_block["list"][next_idx])
                    self.line_num = current_block["start"]
                else:
                    self.block_stack.pop()

            elif current_block["type"] in ("if", "elif", "else", "try"):
                self.block_stack.pop()

            elif current_block["type"] == "function":
                ret_line = self._pop_function_frame()
                if ret_line is not None:
                    self.line_num = ret_line

        else:
            raise PlainLangRuntimeError(f"I don't understand '{line}'")

        self.line_num += 1
        return True

    def _handle_break_or_continue(self, is_break):
        while self.block_stack:
            top = self.block_stack[-1]
            if top["type"] in ("repeat", "while", "foreach"):
                if is_break:
                    end_idx = self.find_matching_end(top["start"])
                    self.block_stack.pop()
                    self.line_num = end_idx + 1
                    return True
                self.line_num = self.find_matching_end(top["start"])
                return True
            self.block_stack.pop()
        return False

    def _handle_exception(self, exc):
        self.set_var("error", str(exc))
        for idx in range(len(self.block_stack) - 1, -1, -1):
            if self.block_stack[idx]["type"] == "try":
                self.block_stack = self.block_stack[:idx]
                catch_idx, kind = self.find_try_branch(self.line_num)
                if kind == "catch":
                    self.block_stack.append({"type": "else", "matched": True})
                    self.line_num = catch_idx + 1
                    return True
        return False

    def run_lines(self, lines):
        self.reset(lines)
        while self.running and self.line_num < len(self.lines):
            try:
                ok = self.execute_line(self.lines[self.line_num])
                if not ok:
                    self.running = False
                    break
            except PlainLangBreak:
                if not self._handle_break_or_continue(True):
                    self.emit(f"Error on line {self.line_num + 1}: 'Break' outside of a loop.")
                    self.running = False
                    break
            except PlainLangContinue:
                if not self._handle_break_or_continue(False):
                    self.emit(f"Error on line {self.line_num + 1}: 'Continue' outside of a loop.")
                    self.running = False
                    break
            except PlainLangReturn as ret:
                self.set_var("last_result", ret.value)
                ret_line = self._pop_function_frame()
                if ret_line is not None:
                    self.line_num = ret_line + 1
                else:
                    self.running = False
                    break
            except Exception as exc:
                if not self._handle_exception(exc):
                    self.emit(f"Error on line {self.line_num + 1}: {exc}")
                    self.running = False
                    break
        self.running = False

    def run_file(self, filename):
        if not os.path.exists(filename):
            self.emit(f"Error: File '{filename}' not found.")
            return
        with open(filename, "r", encoding="utf-8") as f:
            self.run_lines(f.readlines())


def _get_project_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _get_engine_source():
    if not getattr(sys, "frozen", False):
        src_file = os.path.abspath(__file__)
        if src_file.lower().endswith(".py") and os.path.exists(src_file):
            with open(src_file, "r", encoding="utf-8") as f:
                return f.read()

    parts = [
        "import ast, builtins, datetime, json, os, random, re, subprocess, sys, threading, time, urllib.request",
        "import tkinter as tk",
        "from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk",
        "BUILTIN_FUNCTIONS = " + repr(BUILTIN_FUNCTIONS),
        inspect.getsource(_build_builtin_registry),
        "PLAINLANG_BUILTINS = _build_builtin_registry()",
        inspect.getsource(PlainLangReturn),
        inspect.getsource(PlainLangBreak),
        inspect.getsource(PlainLangContinue),
        inspect.getsource(PlainLangRuntimeError),
        inspect.getsource(PlainLangEngine),
    ]
    return "\n\n".join(parts)


def compile_plain_file(plain_path=None, log_callback=print):
    project_dir = _get_project_dir()

    if not plain_path:
        default_todo = os.path.join(project_dir, "todo.plain")
        plain_path = default_todo if os.path.exists(default_todo) else None

    if not plain_path or not os.path.exists(plain_path):
        log_callback("Error: No .plain script found (expected todo.plain or a path argument).")
        return

    abs_plain = os.path.abspath(plain_path)
    with open(abs_plain, "r", encoding="utf-8") as src_f:
        script_text = src_f.read()

    engine_code = _get_engine_source()
    runner_script = os.path.join(project_dir, "_plainlang_entry.py")
    embedded_code = (
        f"{engine_code}\n\n"
        f"EMBEDDED_SCRIPT = {script_text!r}\n\n"
        "def _run_embedded():\n"
        "    if len(sys.argv) > 1:\n"
        "        PlainLangEngine().run_file(sys.argv[1])\n"
        "    else:\n"
        "        PlainLangEngine().run_lines(EMBEDDED_SCRIPT.splitlines())\n\n"
        "if __name__ == '__main__':\n"
        "    _run_embedded()\n"
    )
    with open(runner_script, "w", encoding="utf-8") as f:
        f.write(embedded_code)

    if getattr(sys, "frozen", False):
        pyinstaller_cmd = [sys.executable, "--pyinstaller-runner", runner_script]
    else:
        pyinstaller_cmd = [sys.executable, "-m", "PyInstaller"]

    cmd = pyinstaller_cmd + [
        "--onefile",
        "--noconfirm",
        "--workpath",
        os.path.join(project_dir, "build"),
        "--distpath",
        os.path.join(project_dir, "dist"),
        "--specpath",
        project_dir,
        "--name",
        "plainlang",
        runner_script,
    ]
    log_callback("Compiling: " + " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            log_callback(f"Success! Built {os.path.join(project_dir, 'dist', 'plainlang.exe')}")
        else:
            log_callback(proc.stderr or proc.stdout or "Compilation failed.")
    finally:
        if os.path.exists(runner_script):
            os.remove(runner_script)


class PlainLangStudio:
    SAMPLE_CODE = """# PlainLang Zero-Quotes Builtins Demo
Absolute value of -42 into abs_val
Print Abs: abs_val

Range from 1 to 6 into nums
Sum of nums into total_sum
Maximum of nums into max_val
Minimum of nums into min_val
Print Sum: total_sum Max: max_val Min: min_val

Convert 255 to hex into hex_val
Convert 255 to bin into bin_val
Convert 255 to oct into oct_val
Print Hex: hex_val Bin: bin_val Oct: oct_val

Call len with nums into count_val
Type of nums into nums_type
Print Count: count_val Type: nums_type
"""

    def __init__(self, initial_file=None):
        self.root = tk.Tk()
        self.root.title("PlainLang Studio — Zero-Quotes Builtins Runtime")
        self.root.geometry("1040x700")
        self.root.configure(bg="#1e1e1e")

        self.current_file = initial_file
        self.engine = PlainLangEngine(
            output_callback=self.append_output,
            input_callback=self.prompt_input,
            popup_callback=self.show_popup_threadsafe,
        )
        self.worker_thread = None

        self._build_ui()
        if initial_file and os.path.exists(initial_file):
            self.load_file(initial_file)
        else:
            self.editor.insert("1.0", self.SAMPLE_CODE)
        self.highlight_syntax()

    def _build_ui(self):
        toolbar = tk.Frame(self.root, bg="#252526", pady=6, padx=8)
        toolbar.pack(fill=tk.X)

        btn_style = {"bg": "#333333", "fg": "#ffffff", "relief": tk.FLAT, "padx": 10, "pady": 4}
        tk.Button(toolbar, text="New", command=self.new_file, **btn_style).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="Open .plain", command=self.open_file, **btn_style).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="Save", command=self.save_file, **btn_style).pack(side=tk.LEFT, padx=3)
        tk.Button(toolbar, text="Save As...", command=self.save_file_as, **btn_style).pack(side=tk.LEFT, padx=3)

        tk.Button(
            toolbar, text="Run (F5)", command=self.run_script,
            bg="#2e7d32", fg="#ffffff", relief=tk.FLAT, padx=12, pady=4
        ).pack(side=tk.LEFT, padx=(16, 3))

        tk.Button(
            toolbar, text="Stop", command=self.stop_script,
            bg="#c62828", fg="#ffffff", relief=tk.FLAT, padx=10, pady=4
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            toolbar, text="Build .exe", command=self.compile_exe,
            bg="#1565c0", fg="#ffffff", relief=tk.FLAT, padx=10, pady=4
        ).pack(side=tk.RIGHT, padx=3)

        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg="#1e1e1e", sashwidth=5)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        editor_frame = tk.Frame(paned, bg="#1e1e1e")
        tk.Label(
            editor_frame, text="PLAINLANG EDITOR", bg="#1e1e1e", fg="#9cdcfe",
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill=tk.X, pady=(0, 4))

        self.editor = scrolledtext.ScrolledText(
            editor_frame, wrap=tk.WORD, font=("Consolas", 12),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#ffffff",
            selectbackground="#264f78", undo=True, padx=10, pady=10,
        )
        self.editor.pack(fill=tk.BOTH, expand=True)
        self.editor.bind("<KeyRelease>", lambda e: self.highlight_syntax())
        self.root.bind("<F5>", lambda e: self.run_script())
        self.root.bind("<Control-s>", lambda e: self.save_file())

        self.editor.tag_configure("keyword", foreground="#569cd6", font=("Consolas", 12, "bold"))
        self.editor.tag_configure("control", foreground="#c586c0", font=("Consolas", 12, "bold"))
        self.editor.tag_configure("comment", foreground="#6a9955", font=("Consolas", 12, "italic"))

        paned.add(editor_frame, width=620)

        right_paned = tk.PanedWindow(paned, orient=tk.VERTICAL, bg="#1e1e1e", sashwidth=5)

        console_frame = tk.Frame(right_paned, bg="#1e1e1e")
        hdr = tk.Frame(console_frame, bg="#1e1e1e")
        hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            hdr, text="LIVE OUTPUT CONSOLE", bg="#1e1e1e", fg="#4ec9b0",
            font=("Segoe UI", 9, "bold")
        ).pack(side=tk.LEFT)
        tk.Button(
            hdr, text="Clear", command=self.clear_output,
            bg="#333333", fg="#cccccc", relief=tk.FLAT, font=("Segoe UI", 8), padx=6
        ).pack(side=tk.RIGHT)

        self.console = scrolledtext.ScrolledText(
            console_frame, wrap=tk.WORD, font=("Consolas", 11),
            bg="#141414", fg="#cccccc", state=tk.DISABLED, padx=8, pady=8,
        )
        self.console.pack(fill=tk.BOTH, expand=True)
        right_paned.add(console_frame, height=390)

        vars_frame = tk.Frame(right_paned, bg="#1e1e1e")
        tk.Label(
            vars_frame, text="LIVE VARIABLES & OBJECTS", bg="#1e1e1e", fg="#dcdcaa",
            font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill=tk.X, pady=(0, 4))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#141414", foreground="#d4d4d4", fieldbackground="#141414")
        style.configure("Treeview.Heading", background="#252526", foreground="#ffffff")

        self.var_tree = ttk.Treeview(vars_frame, columns=("value", "type"), show="tree headings")
        self.var_tree.heading("#0", text="Name")
        self.var_tree.heading("value", text="Value")
        self.var_tree.heading("type", text="Type")
        self.var_tree.column("#0", width=130)
        self.var_tree.column("value", width=160)
        self.var_tree.column("type", width=70)
        self.var_tree.pack(fill=tk.BOTH, expand=True)
        right_paned.add(vars_frame, height=220)

        paned.add(right_paned)

        self.status_var = tk.StringVar(value="Ready — PlainLang Zero-Quotes Builtins Runtime")
        tk.Label(
            self.root, textvariable=self.status_var, bg="#007acc", fg="#ffffff",
            anchor="w", padx=10, pady=3, font=("Segoe UI", 9)
        ).pack(fill=tk.X)

    def highlight_syntax(self):
        for tag in ("keyword", "control", "comment"):
            self.editor.tag_remove(tag, "1.0", tk.END)

        lines = self.editor.get("1.0", tk.END).splitlines()
        controls = (
            "if ", "else if ", "otherwise if ", "else", "otherwise",
            "while ", "repeat ", "for each ", "define ", "try", "catch", "except",
            "break", "stop loop", "continue", "skip iteration", "return ", "end"
        )
        keywords = (
            "print ", "set key ", "set ", "add ", "subtract ", "multiply ", "divide ", "modulo ",
            "ask ", "show popup ", "create list ", "create object ", "append ", "pop from ",
            "get item ", "length of ", "sort list ", "reverse list ", "remove ",
            "uppercase ", "lowercase ", "trim ", "split ", "join ", "replace ",
            "parse json ", "to json ", "check file ", "save ", "load ",
            "fetch ", "run command ", "get current time into ",
            "wait ", "pick random between ", "do ",
            "call ", "absolute value of ", "round ", "power ", "divmod ",
            "maximum of ", "minimum of ", "sum of ", "range from ", "convert ",
            "character of ", "unicode of ", "type of ", "hash of ", "id of ",
            "check all ", "check any ", "enumerate ", "zip ", "reverse ", "sort ",
            "slice ", "has attribute ", "get attribute ", "set attribute ",
            "delete attribute ", "check callable ", "check instance of ",
            "check subclass of ", "iterate ", "next item of ", "format ",
            "open file ", "help on ", "eval python ", "run python ",
            "filter ", "map ", "import module ", "globals into ", "locals into ",
            "dir of ", "vars of ", "memoryview of ", "object into ",
            "super into ", "property ", "classmethod ", "staticmethod ", "breakpoint"
        )

        for idx, text in enumerate(lines, start=1):
            stripped = text.lstrip()
            lower = stripped.lower()
            if stripped.startswith("#"):
                self.editor.tag_add("comment", f"{idx}.0", f"{idx}.end")
                continue

            prefix_len = len(text) - len(stripped)
            for kw in controls:
                if lower == kw or lower.startswith(kw):
                    self.editor.tag_add("control", f"{idx}.0", f"{idx}.{prefix_len + len(kw)}")
                    break
            else:
                for kw in keywords:
                    if lower.startswith(kw):
                        self.editor.tag_add("keyword", f"{idx}.0", f"{idx}.{prefix_len + len(kw)}")
                        break

    def append_output(self, text):
        def _write():
            self.console.configure(state=tk.NORMAL)
            self.console.insert(tk.END, text + "\n")
            self.console.see(tk.END)
            self.console.configure(state=tk.DISABLED)
            self.refresh_variables()

        self.root.after(0, _write)

    def show_popup_threadsafe(self, msg):
        done = threading.Event()

        def _show():
            messagebox.showinfo("PlainLang", msg, parent=self.root)
            done.set()

        self.root.after(0, _show)
        done.wait()

    def clear_output(self):
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.configure(state=tk.DISABLED)

    def prompt_input(self, question):
        result = {"value": None}
        done = threading.Event()

        def _ask():
            result["value"] = simpledialog.askstring("PlainLang Input", question, parent=self.root)
            done.set()

        self.root.after(0, _ask)
        done.wait()
        return result["value"]

    def refresh_variables(self):
        for item in self.var_tree.get_children():
            self.var_tree.delete(item)
        for k, v in self.engine.variables.items():
            self.var_tree.insert("", tk.END, text=k, values=(str(v), type(v).__name__))

    def run_script(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Running", "A script is already running. Click Stop first.")
            return

        self.clear_output()
        code_lines = self.editor.get("1.0", tk.END).splitlines()
        self.status_var.set("Running script...")

        def _worker():
            self.engine.run_lines(code_lines)
            self.root.after(0, self.refresh_variables)
            self.root.after(0, lambda: self.status_var.set("Execution finished."))

        self.worker_thread = threading.Thread(target=_worker, daemon=True)
        self.worker_thread.start()

    def stop_script(self):
        self.engine.stop()
        self.status_var.set("Execution stopped by user.")

    def new_file(self):
        self.current_file = None
        self.editor.delete("1.0", tk.END)
        self.status_var.set("New untitled script")

    def load_file(self, path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.current_file = path
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", content)
        self.highlight_syntax()
        self.status_var.set(f"Opened: {path}")

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("PlainLang Scripts", "*.plain"), ("All Files", "*.*")]
        )
        if path:
            self.load_file(path)

    def save_file(self):
        if not self.current_file:
            return self.save_file_as()
        with open(self.current_file, "w", encoding="utf-8") as f:
            f.write(self.editor.get("1.0", tk.END).rstrip() + "\n")
        self.status_var.set(f"Saved: {self.current_file}")
        return True

    def save_file_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".plain",
            filetypes=[("PlainLang Scripts", "*.plain"), ("All Files", "*.*")],
        )
        if path:
            self.current_file = path
            return self.save_file()
        return False

    def compile_exe(self):
        if self.current_file and not self.save_file():
            return
        target_script = self.current_file
        if not target_script:
            project_dir = _get_project_dir()
            todo_path = os.path.join(project_dir, "todo.plain")
            with open(todo_path, "w", encoding="utf-8") as f:
                f.write(self.editor.get("1.0", tk.END).rstrip() + "\n")
            self.current_file = todo_path
            target_script = todo_path

        self.clear_output()
        self.status_var.set("Building plainlang.exe via PyInstaller...")

        def _build():
            compile_plain_file(target_script, log_callback=self.append_output)
            self.root.after(0, lambda: self.status_var.set("Build process complete."))

        threading.Thread(target=_build, daemon=True).start()

    def start(self):
        self.root.mainloop()


def main():
    args = sys.argv[1:]
    if not args:
        PlainLangStudio().start()
    elif args[0] == "--studio":
        target = args[1] if len(args) > 1 else None
        PlainLangStudio(target).start()
    elif len(args) >= 2 and args[0] == "--compile":
        compile_plain_file(args[1])
    elif args[0] == "--compile":
        compile_plain_file(None)
    else:
        PlainLangEngine().run_file(args[0])


if __name__ == "__main__":
    main()