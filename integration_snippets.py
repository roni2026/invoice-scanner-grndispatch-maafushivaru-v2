"""
integration_snippets.py
------------------------
Not a standalone script — copy/adapt these three pieces into
maafushivaru_hub.py. They assume:
  - your results table is a ttk.Treeview (Dispatch / Renamer tab), one row
    per document, with a "SUPPLIER" and "INVOICE #" column, and each row's
    `iid` maps to a dict holding at least {"raw_text": ..., "supplier": ...,
    "invoice_no": ...} — adjust the lookup to whatever your row-store is
    actually called.
  - config.json is loaded once into self.config and you already have a
    `self.save_config()` — if not, use supplier_learning.save_config().

pip install: nothing extra, this only uses stdlib + your existing tkinter.
"""

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import supplier_learning as sl


# --------------------------------------------------------------------------- #
# 1) Right-click menu on a results row: "Fix Supplier Name" + "Add Supplier"
# --------------------------------------------------------------------------- #

class SupplierContextMenuMixin:
    """Mix this into your Dispatch/Renamer tab class (or just copy the
    methods in), then call self._bind_supplier_context_menu(self.tree)
    once, after you create the Treeview."""

    def _bind_supplier_context_menu(self, tree: ttk.Treeview):
        self._ctx_tree = tree
        tree.bind("<Button-3>", self._on_row_right_click)  # Windows/Linux
        tree.bind("<Button-2>", self._on_row_right_click)  # some macOS setups

    def _on_row_right_click(self, event):
        tree = event.widget
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        tree.selection_set(row_id)

        menu = tk.Menu(tree, tearoff=0)
        menu.add_command(
            label="Fix Supplier Name...",
            command=lambda: self._fix_supplier_dialog(row_id),
        )
        menu.add_command(
            label="Add New Supplier...",
            command=lambda: self._add_supplier_dialog(row_id),
        )
        menu.tk_popup(event.x_root, event.y_root)

    # ---- Fix Supplier Name: teaches the matcher from a correction ----
    def _fix_supplier_dialog(self, row_id):
        row = self.row_store[row_id]  # <-- your actual row dict lookup here
        wrong_guess = row.get("supplier", "")

        # simple picker — swap for a searchable combobox if you have one
        correct = simpledialog.askstring(
            "Fix Supplier Name",
            f"OCR guessed:  {wrong_guess or '(blank)'}\n\n"
            "Type the CORRECT supplier name exactly as it appears in "
            "config.json's supplier list:",
        )
        if not correct:
            return
        correct = correct.strip().upper()

        if correct not in self.config.get("suppliers", []):
            if not messagebox.askyesno(
                "Unknown supplier",
                f"'{correct}' isn't in the supplier list yet.\n"
                "Open 'Add New Supplier' instead?",
            ):
                return
            self._add_supplier_dialog(row_id, prefill_name=correct)
            return

        report = sl.learn_from_correction(
            extracted_text=row.get("raw_text", ""),
            wrong_supplier_guess=wrong_guess,
            correct_supplier=correct,
            extracted_invoice_no=row.get("invoice_no"),
            cfg=self.config,
        )
        sl.save_config(self.config)

        # update the row + tree cell in place
        row["supplier"] = correct
        self._ctx_tree.set(row_id, "SUPPLIER", correct)

        msg_bits = [f"Supplier set to {correct}."]
        if report["alias_added"]:
            msg_bits.append(f"Learned alias: \"{report['alias_added']}\" -> {correct}")
        if report["format_added"]:
            msg_bits.append(f"Learned new invoice format: {report['format_added']}")
        if report["format_bumped"]:
            msg_bits.append(f"Confirmed existing invoice format: {report['format_bumped']}")
        messagebox.showinfo("Learned", "\n".join(msg_bits))

    # ---- Add New Supplier: onboards a supplier the matcher has never seen ----
    def _add_supplier_dialog(self, row_id=None, prefill_name: str = ""):
        row = self.row_store[row_id] if row_id else {}
        name = simpledialog.askstring(
            "Add New Supplier", "Supplier name (as it should appear in Excel):",
            initialvalue=prefill_name or row.get("supplier", ""),
        )
        if not name:
            return
        alias_raw = simpledialog.askstring(
            "Add New Supplier",
            "Known alternate spellings, comma-separated (optional):",
        ) or ""
        aliases = [a.strip() for a in alias_raw.split(",") if a.strip()]

        sample_invoice = simpledialog.askstring(
            "Add New Supplier",
            "Paste one real invoice number from this supplier's document "
            "(used to build the regex pattern), e.g. 'INV-2026-0451':",
            initialvalue=row.get("invoice_no", ""),
        )

        result = sl.add_supplier(
            canonical_name=name,
            aliases=aliases,
            sample_invoice_no=sample_invoice,
            cfg=self.config,
        )
        sl.save_config(self.config)

        if row_id:
            row["supplier"] = result["supplier"]
            self._ctx_tree.set(row_id, "SUPPLIER", result["supplier"])

        fmt_msg = f"\nInvoice pattern: {result['format']['regex']}" if result["format"] else \
                  "\n(No sample invoice # given — add one later from Settings > Suppliers.)"
        messagebox.showinfo("Supplier added", f"Added '{result['supplier']}'.{fmt_msg}")


# --------------------------------------------------------------------------- #
# 2) Dashboard: live "PDFs waiting" counter
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# 1b) Settings tab checkbox + per-document auto-fix call
# --------------------------------------------------------------------------- #
#
# Settings tab: add one checkbox bound to a BooleanVar, and on toggle write
# it straight to config.json:
#
#   self.auto_fix_var = tk.BooleanVar(
#       value=self.config["app_settings"].get("auto_fix_supplier_by_invoice_pattern", False)
#   )
#   ttk.Checkbutton(
#       settings_frame,
#       text="Auto-fix supplier name using invoice # pattern (when unambiguous)",
#       variable=self.auto_fix_var,
#       command=lambda: (
#           self.config["app_settings"].__setitem__(
#               "auto_fix_supplier_by_invoice_pattern", self.auto_fix_var.get()
#           ),
#           sl.save_config(self.config),
#       ),
#   ).pack(anchor="w")
#
# Then in the extraction pipeline, right after you've got a supplier guess
# and an invoice number for a document (before writing the row to the
# table), call this once per batch/run using a CACHED ownership index
# (rebuilding it is cheap but no need to do it per-document):
#
#   if self.config["app_settings"].get("auto_fix_supplier_by_invoice_pattern"):
#       idx = sl.build_ownership_index(self.config)          # once per batch
#       result = sl.cross_match_and_autocorrect(
#           supplier_guess=row["supplier"],
#           invoice_no_raw=row["invoice_no"],
#           cfg=self.config,
#           ownership_index=idx,
#       )
#       if result["action"] != "none":
#           row["supplier"] = result["supplier"]
#           row["invoice_no"] = result["invoice_no"]
#           self.logger.info(result["reason"])   # full audit trail in LOGS/
#           # low-confidence rows can still get flagged for review as usual;
#           # this only ever fires on an UNAMBIGUOUS single-owner pattern


class PendingPdfCounter:
    """Add to your Dashboard tab's __init__ (after self.config is loaded):

        self.pending_counter = PendingPdfCounter(
            parent=dashboard_frame,
            get_folder=lambda: self.config["app_settings"]["processed_transfer_path"],
        )
        self.pending_counter.grid(row=..., column=...)   # place it wherever

    It refreshes itself every 4 seconds via Tk's `after`, no extra thread
    needed since it's just a directory listing.
    """

    REFRESH_MS = 4000

    def __init__(self, parent, get_folder, label_prefix="PDFs waiting in transfer folder: "):
        self.get_folder = get_folder
        self.label_prefix = label_prefix
        self.var = tk.StringVar(value=f"{label_prefix}...")
        self.label = ttk.Label(parent, textvariable=self.var, font=("Segoe UI", 10, "bold"))
        self._after_id = None
        self._tick()

    def grid(self, *a, **kw):
        self.label.grid(*a, **kw)

    def pack(self, *a, **kw):
        self.label.pack(*a, **kw)

    def _tick(self):
        folder = self.get_folder()
        count = sl.count_pending_pdfs(folder)
        self.var.set(f"{self.label_prefix}{count}")
        self._after_id = self.label.after(self.REFRESH_MS, self._tick)

    def stop(self):
        if self._after_id:
            self.label.after_cancel(self._after_id)
