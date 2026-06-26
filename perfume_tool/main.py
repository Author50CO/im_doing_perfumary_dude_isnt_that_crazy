import tkinter as tk

from .app import PerfumeCalculatorApp


def main():
    root = tk.Tk()
    app = PerfumeCalculatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()