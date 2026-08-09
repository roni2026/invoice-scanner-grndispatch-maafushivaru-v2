"""
apply_fix.py — Patch maafushivaru_hub.py to fix tree editing + add right-click menu.
Run this script inside your project folder (same folder as maafushivaru_hub.py).
"""
import os
import sys

FILE = "maafushivaru_hub.py"
if not os.path.exists(FILE):
    print(f"ERROR: {FILE} not found in current directory.")
    print(f"Current dir: {os.getcwd()}")
    sys.exit(1)

with open(FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

# ------------------------------------------------------------------
# 1. Fix _bind_tree_mousewheel (lines ~2962-2969)
# ------------------------------------------------------------------
BIND_OLD_START = None
BIND_OLD_END = None
for i, line in enumerate(lines):
    if "def _bind_tree_mousewheel" in line:
        BIND_OLD_START = i
    if BIND_OLD_START is not None and BIND_OLD_END is None:
        if "def _build_dashboard_tab" in line:
            BIND_OLD_END = i
            break

if BIND_OLD_START is None or BIND_OLD_END is None:
    print("ERROR: Could not find _bind_tree_mousewheel method.")
    sys.exit(1)

BIND_NEW = [
    "    def _bind_tree_mousewheel(self, tree: ttk.Treeview):\n",
    "        def _on_mw(event):\n",
    '            tree.yview_scroll(int(-1 * (event.delta / 120)), "units")\n',
    '            return "break"\n',
    "\n",
    '        tree.bind("<MouseWheel>", _on_mw)\n',
    '        tree.bind("<Button-4>",  lambda e: tree.yview_scroll(-1, "units") or "break")   # Linux\n',
    '        tree.bind("<Button-5>",  lambda e: tree.yview_scroll( 1, "units") or "break")   # Linux\n',
    "\n",
]

# ------------------------------------------------------------------
# 2. Fix _make_tree_editable (lines ~4462-4646)
# ------------------------------------------------------------------
EDIT_OLD_START = None
EDIT_OLD_END = None
for i, line in enumerate(lines):
    if "def _make_tree_editable" in line:
        EDIT_OLD_START = i
    if EDIT_OLD_START is not None and EDIT_OLD_END is None:
        if "def _show_dry_run_preview" in line:
            EDIT_OLD_END = i
            break

if EDIT_OLD_START is None or EDIT_OLD_END is None:
    print("ERROR: Could not find _make_tree_editable method.")
    sys.exit(1)

EDIT_NEW = [
    '    def _make_tree_editable(self, tree, on_edit_callback=None, editable_cols=None, pre_edit_fn=None):\n',
    '        """\n',
    '        Make Treeview cells editable by double-clicking or right-clicking.\n',
    '\n',
    '        Column 0 can be used for PDF preview when a preview callback\n',
    '        is assigned to tree._edit_on_preview_cb.\n',
    '        """\n',
    '\n',
    '        tree._edit_editable_cols = editable_cols\n',
    '        tree._edit_on_edit_cb = on_edit_callback\n',
    '        tree._edit_pre_edit_fn = pre_edit_fn\n',
    '        tree._edit_entry = None\n',
    '        tree._edit_active_row = None\n',
    '        tree._edit_active_col = None\n',
    '\n',
    '        def close_editor():\n',
    '            entry = getattr(tree, "_edit_entry", None)\n',
    '            if entry is not None:\n',
    '                try:\n',
    '                    if entry.winfo_exists():\n',
    '                        entry.destroy()\n',
    '                except Exception:\n',
    '                    pass\n',
    '            tree._edit_entry = None\n',
    '            tree._edit_active_row = None\n',
    '            tree._edit_active_col = None\n',
    '\n',
    '        def open_editor(row_id, col_id, from_event="double"):\n',
    '            """Open an inline Entry editor for a specific tree cell."""\n',
    '            if not tree.winfo_exists():\n',
    '                return\n',
    '\n',
    '            ci = int(col_id[1:]) - 1\n',
    '\n',
    '            # Column 0 = PDF preview when a preview callback exists.\n',
    '            if ci == 0:\n',
    '                preview_cb = getattr(tree, "_edit_on_preview_cb", None)\n',
    '                if preview_cb is not None:\n',
    '                    preview_cb(row_id)\n',
    '                    return\n',
    '\n',
    '            # Check whether this column is editable.\n',
    '            if (\n',
    '                tree._edit_editable_cols is not None\n',
    '                and ci not in tree._edit_editable_cols\n',
    '            ):\n',
    '                return\n',
    '\n',
    '            values = list(tree.item(row_id, "values"))\n',
    '            if ci >= len(values):\n',
    '                return\n',
    '\n',
    '            current_value = str(values[ci])\n',
    '\n',
    '            # If an editor is already open on the SAME cell, don\'t re-create it.\n',
    '            if tree._edit_active_row == row_id and tree._edit_active_col == ci:\n',
    '                entry = getattr(tree, "_edit_entry", None)\n',
    '                if entry and entry.winfo_exists():\n',
    '                    entry.focus_force()\n',
    '                    return\n',
    '\n',
    '            def create_editor():\n',
    '                if not tree.winfo_exists():\n',
    '                    return\n',
    '\n',
    '                bbox = tree.bbox(row_id, col_id)\n',
    '                if not bbox:\n',
    '                    return\n',
    '\n',
    '                close_editor()\n',
    '\n',
    '                bx, by, bw, bh = bbox\n',
    '                display_value = current_value\n',
    '\n',
    '                if tree._edit_pre_edit_fn:\n',
    '                    try:\n',
    '                        display_value = tree._edit_pre_edit_fn(ci, current_value)\n',
    '                    except Exception:\n',
    '                        display_value = current_value\n',
    '\n',
    '                var = tk.StringVar(value=display_value)\n',
    '\n',
    '                entry = tk.Entry(\n',
    '                    tree,\n',
    '                    textvariable=var,\n',
    '                    background=PANEL2,\n',
    '                    foreground=TEXT,\n',
    '                    insertbackground=TEXT,\n',
    '                    relief="flat",\n',
    '                    font=("Segoe UI", 9, "bold"),\n',
    '                    bd=2,\n',
    '                    highlightthickness=1,\n',
    '                    highlightbackground=ACCENT,\n',
    '                    highlightcolor=ACCENT,\n',
    '                )\n',
    '\n',
    '                tree._edit_entry = entry\n',
    '                tree._edit_active_row = row_id\n',
    '                tree._edit_active_col = ci\n',
    '\n',
    '                entry.place(x=bx, y=by, width=bw, height=bh)\n',
    '                entry.focus_force()\n',
    '                entry.select_range(0, tk.END)\n',
    '\n',
    '                finished = [False]\n',
    '\n',
    '                def commit(event=None):\n',
    '                    if finished[0]:\n',
    '                        return "break"\n',
    '                    finished[0] = True\n',
    '                    new_value = var.get().strip()\n',
    '                    close_editor()\n',
    '\n',
    '                    if new_value == display_value:\n',
    '                        return "break"\n',
    '                    if new_value == "":\n',
    '                        return "break"\n',
    '\n',
    '                    callback = tree._edit_on_edit_cb\n',
    '                    if callback:\n',
    '                        callback(row_id, ci, current_value, new_value)\n',
    '                    else:\n',
    '                        values[ci] = new_value\n',
    '                        tree.item(row_id, values=values)\n',
    '                    return "break"\n',
    '\n',
    '                def cancel(event=None):\n',
    '                    finished[0] = True\n',
    '                    close_editor()\n',
    '                    return "break"\n',
    '\n',
    '                entry.bind("<Return>", commit)\n',
    '                entry.bind("<KP_Enter>", commit)\n',
    '                entry.bind("<Escape>", cancel)\n',
    '\n',
    '            tree.after_idle(create_editor)\n',
    '\n',
    '        def on_double_click(event):\n',
    '            if tree.identify("region", event.x, event.y) != "cell":\n',
    '                return "break"\n',
    '            col_id = tree.identify_column(event.x)\n',
    '            row_id = tree.identify_row(event.y)\n',
    '            if not row_id or not col_id:\n',
    '                return "break"\n',
    '            open_editor(row_id, col_id, from_event="double")\n',
    '            return "break"\n',
    '\n',
    '        def on_right_click(event):\n',
    '            """Show a context menu on right-click with Edit / Preview options."""\n',
    '            if tree.identify("region", event.x, event.y) != "cell":\n',
    '                return\n',
    '            col_id = tree.identify_column(event.x)\n',
    '            row_id = tree.identify_row(event.y)\n',
    '            if not row_id or not col_id:\n',
    '                return\n',
    '\n',
    '            ci = int(col_id[1:]) - 1\n',
    '            menu = tk.Menu(\n',
    '                tree, tearoff=0, bg=PANEL2, fg=TEXT,\n',
    '                activebackground=ACCENT, activeforeground="white",\n',
    '                font=("Segoe UI", 9)\n',
    '            )\n',
    '\n',
    '            # Preview option for column 0\n',
    '            preview_cb = getattr(tree, "_edit_on_preview_cb", None)\n',
    '            if ci == 0 and preview_cb is not None:\n',
    '                menu.add_command(label="👁 Preview PDF", command=lambda: preview_cb(row_id))\n',
    '                menu.add_separator()\n',
    '\n',
    '            # Edit option for editable columns\n',
    '            is_editable = (\n',
    '                tree._edit_editable_cols is None\n',
    '                or ci in tree._edit_editable_cols\n',
    '            )\n',
    '            if is_editable:\n',
    '                menu.add_command(\n',
    '                    label="✏️ Edit",\n',
    '                    command=lambda: open_editor(row_id, col_id, from_event="right")\n',
    '                )\n',
    '            else:\n',
    '                menu.add_command(label="🔒 Read-only", state="disabled")\n',
    '\n',
    '            menu.tk_popup(event.x_root, event.y_root)\n',
    '\n',
    '        # Use add="+" so this doesn't destroy any other Treeview bindings.\n',
    '        tree.bind("<Double-1>", on_double_click, add="+")\n',
    '        tree.bind("<Button-3>", on_right_click, add="+")   # Windows / Linux right-click\n',
    '        tree.bind("<Button-2>", on_right_click, add="+")   # macOS right-click (Ctrl+click)\n',
    '\n',
    '        # Clicking somewhere else finishes the edit cleanly.\n',
    '        def click_elsewhere(event):\n',
    '            entry = getattr(tree, "_edit_entry", None)\n',
    '            if entry is None:\n',
    '                return\n',
    '            if event.widget is entry:\n',
    '                return\n',
    '            # Don\'t close if we\'re clicking on the same cell that has the editor.\n',
    '            try:\n',
    '                if tree.identify("region", event.x, event.y) == "cell":\n',
    '                    rid = tree.identify_row(event.y)\n',
    '                    cid = tree.identify_column(event.x)\n',
    '                    if rid and cid:\n',
    '                        ci = int(cid[1:]) - 1\n',
    '                        if rid == tree._edit_active_row and ci == tree._edit_active_col:\n',
    '                            return\n',
    '            except Exception:\n',
    '                pass\n',
    '            tree.after_idle(close_editor)\n',
    '\n',
    '        tree.bind("<Button-1>", click_elsewhere, add="+")\n',
    '\n',
]

# ------------------------------------------------------------------
# Apply patches
# ------------------------------------------------------------------
new_lines = lines[:BIND_OLD_START] + BIND_NEW + lines[BIND_OLD_END:EDIT_OLD_START] + EDIT_NEW + lines[EDIT_OLD_END:]

# Backup original
backup = FILE + ".backup"
with open(backup, "w", encoding="utf-8") as f:
    f.writelines(lines)
print(f"Backup saved to: {backup}")

# Write fixed file
with open(FILE, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print(f"Patched {FILE} successfully!")
print("Changes made:")
print("  1. Fixed _bind_tree_mousewheel Linux bindings")
print("  2. Refactored _make_tree_editable with open_editor() helper")
print("  3. Added right-click context menu (Edit / Preview PDF)")
print("  4. Added active-cell tracking to prevent accidental editor close")
print("\nRun:  python maafushivaru_hub.py")
