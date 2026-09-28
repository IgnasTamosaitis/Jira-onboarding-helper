"""Small UI updates that leave selection and scroll position in place."""


def update_listbox(listbox, rows):
    """Update only changed labels/colors, preserving the operator's viewport."""
    previous = listbox.get(0, "end")
    selection = listbox.curselection()
    active = listbox.index("active")
    top = listbox.yview()[0]
    changed = False
    for index, (label, color) in enumerate(rows):
        if index >= len(previous):
            listbox.insert("end", label)
            changed = True
        elif previous[index] != label:
            listbox.delete(index)
            listbox.insert(index, label)
            changed = True
        if listbox.itemcget(index, "fg") != color:
            listbox.itemconfigure(index, fg=color)
    if len(previous) > len(rows):
        listbox.delete(len(rows), "end")
        changed = True
    if changed:
        listbox.selection_clear(0, "end")
        for index in selection:
            if index < len(rows):
                listbox.selection_set(index)
        listbox.activate(active)
        listbox.yview_moveto(top)


def update_readonly_text(box, text):
    if box.get("1.0", "end-1c") == text:
        return
    top = box.yview()[0]
    box.configure(state="normal")
    box.delete("1.0", "end")
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.yview_moveto(top)
