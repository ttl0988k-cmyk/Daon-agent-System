# -*- coding: utf-8 -*-
import sys

def test_tk():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.lift()
        root.focus_force()
        print("TKINTER_OK")
        root.destroy()
        return True
    except Exception as e:
        print("TKINTER_ERR:", e)
        return False

if __name__ == '__main__':
    test_tk()
