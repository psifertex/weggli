# If run from a snippet, then binaryninja is already imported.
SNIPPET = "binaryninja" in globals()

from binaryninja import *
from binaryninjaui import getThemeColor, ThemeColor
from PySide6.QtCore import QSettings
from ansi2html import Ansi2HTMLConverter

import typing
import weggli


class WeggliPlugin(object):
    def __init__(self, bv: BinaryView, print_code: bool = False, output_format: str = "Log"):
        self.bv = bv
        self.print_code = print_code
        self.output_format = output_format

    def get_function(self, name: str) -> typing.Optional[Function]:
        for f in self.bv.functions:
            if f.name == name:
                return f
        return None

    def decompile(self, func: Function) -> str:
        # TODO: Include comments with addresses for easier navigation
        Settings().set_string("rendering.hlil.scopingStyle", "bracesNewLine")
        settings = DisassemblySettings()
        settings.set_option(DisassemblyOption.ShowAddress, False)
        settings.set_option(DisassemblyOption.WaitForIL, True)

        obj = lineardisassembly.LinearViewObject.language_representation(
            self.bv, settings
        )
        cursor_end = lineardisassembly.LinearViewCursor(obj)
        cursor_end.seek_to_address(func.highest_address)
        end_lines = self.bv.get_next_linear_disassembly_lines(cursor_end)
        cursor_end.seek_to_address(func.highest_address)
        start_lines = self.bv.get_previous_linear_disassembly_lines(cursor_end)
        lines = start_lines + end_lines

        return "\n".join(
            "".join(
                str(token)
                for token in line.contents.tokens
                if token.type != InstructionTextTokenType.TagToken
            )
            for line in lines
        )

    def xrefs_to(self, f: Function) -> typing.Generator[Function, None, None]:
        for xref in self.bv.get_callers(f.start):
            yield from self.bv.get_functions_containing(xref.address)

    def run_query(self, query: str):
        # TODO: Remove the below section when the python API does the cleanup the regular API does
        if query.find(";") < 0:
            log_warn("The query did not have a semi-colon, naively appending one.")
            query += ";"
        if query.find("{") < 0 or query.find("}") < 0:
            log_warn("The query did not have braces, naively adjusting by adding beginning and ending ones.")
            query = "{" + query + "}"
        # End section

        qt = weggli.parse_query(query)

        identifiers = weggli.identifiers(qt)
        referenced_funcs = list(
            filter(lambda f: f != None, [self.get_function(i) for i in identifiers])
        )
        if self.output_format == "Report Tab":
            fg = getThemeColor(ThemeColor.CommentColor).name()
            bg = getThemeColor(ThemeColor.LinearDisassemblyBlockColor).name()
            red = getThemeColor(ThemeColor.RedStandardHighlightColor).name()
            report='''<!DOCTYPE html>
    <html>
    <head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
    <title>Weggli Report</title>
    <style type="text/css">
    .ansi2html-content { display: inline; white-space: pre-wrap; word-wrap: break-word; }
    .body_foreground { color: ''' + fg + '''; }
    .body_background { background-color: ''' + bg + '''; }
    .inv_foreground { color: ''' + bg + '''; }
    .inv_background { background-color: ''' + fg + '''; }
    .ansi31 { color: ''' + red + ''' }
    </style>
    </head>
    <body>
    '''
            report += "<h2>Weggli Query</h2>"
            report += f"<dl><dt>Search Query</dt><dd><pre>{query}</pre></dd></dl>"

        if len(referenced_funcs) > 0:
            work = set(self.xrefs_to(referenced_funcs[0]))

            for f in referenced_funcs[1:]:
                work.intersection_update(self.xrefs_to(f))

            log_info(f"Searching through {len(work)} functions..")
            if self.output_format == "Report Tab":
                report += f"<p>Searching through {len(work)} functions..</p>"
            for target in work:
                if not target:
                    continue
                code = self.decompile(target)
                if code != None:
                    results = weggli.matches(qt, code)
                    if len(results) > 0:
                        if self.output_format == "Log":
                            log_info(
                                f"{len(results)} matches in {target.symbol.full_name} @ {hex(target.start)}"
                            )
                        if self.output_format == "Report Tab":
                            report += f'<div class="result">{len(results)} matches in <a href="binaryninja://?expr={target.symbol.full_name}">{target.symbol.full_name}</a> @ <a href="binaryninja://?expr={hex(target.start)}">{hex(target.start)}</a></div>'
                        if self.print_code:
                            conv = Ansi2HTMLConverter()
                            for r in results:
                                if self.output_format == "Log":
                                    pretty_code = weggli.display(r, code, color = False)
                                    log_info(pretty_code)
                                if self.output_format == "Report Tab":
                                    pretty_code = weggli.display(r, code, color = True)
                                    report += f"<div class=\"ansi2html-content\"><pre><code>{conv.convert(pretty_code, full = False, ensure_trailing_newline = True)}</code></pre></div>"
                else:
                    log_error(f"Decompilation failed for {target.name}. Skipping..")
        if self.output_format == "Report Tab":
            report += "</body></html>"
            self.bv.show_html_report("Weggli Results", report)


def run_query(bv: BinaryView):
    qsettings = QSettings("Weggli", "Weggli")
    if qsettings.contains("ui/querytext"):
        default = qsettings.value("ui/querytext")
    else:
        default = ""
    query_input = MultilineTextField('Weggli Query Text', default = default)
    # TODO: Add tags back when weggli has a better parsable output available via Python
    #output_format = ChoiceField('Output Format', ['Report Tab', 'Log', 'Create Tags'], default=0)
    choices = ["Report Tab", "Log"]
    default = choices.index(Settings().get_string("weggli.default_output"))
    output_format = ChoiceField('Output Format', choices, default = default)
    ok = get_form_input([output_format, query_input], 'Weggli Search')
    if not ok:
        return False
    qsettings.setValue("ui/querytext", query_input.result)
    w = WeggliPlugin(bv, print_code = Settings().get_bool("weggli.show_code"), output_format = output_format.choices[output_format.result])
    w.run_query(query_input.result)


if not SNIPPET:
    Settings().register_group("weggli", "Weggli Search")
    Settings().register_setting("weggli.show_code", """
    {
        "title" : "Show Code Matches",
        "type" : "boolean",
        "default" : true,
        "description" : "Whether to show full code matches in weggli searches",
        "ignore" : ["SettingsProjectScope", "SettingsResourceScope"]
    }
    """)
    Settings().register_setting("weggli.default_output", """
    {
        "title" : "Output Mode",
        "type" : "string",
        "default" : "Report Tab",
        "description" : "Default selection for output from weggli",
        "enum" : ["Report Tab", "Log"],
        "enumDescriptions" : [
            "Create a new tab with clickable links for all results found",
            "Just output to the log iwindow"]
    }
    """)
    PluginCommand.register("Weggli Query", "Run a weggli query", run_query)
else:
    # bv is injected into globals in the snippet / python console.
    run_query(bv)
