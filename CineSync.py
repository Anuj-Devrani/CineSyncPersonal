import os
import sys
import subprocess
import getpass
import logging
import argparse
from textual.app import App, ComposeResult
from textual.widgets import (
    Button,
    Header,
    Footer,
    Static,
    Pretty,
    RichLog,
    SelectionList,
    Input,
    Tree,
)
from textual.containers import Container, Vertical, Horizontal
from textual.screen import Screen
from textual.binding import Binding
from MediaHub.processors.symlink_creator import create_symlinks
from MediaHub.config.config import get_directories

# Append the parent directory to the system path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local imports from MediaHub
from MediaHub.utils.logging_utils import log_message
from MediaHub.processors.db_utils import (
    get_database_stats,
    vacuum_database,
    verify_database_integrity,
    export_database,
    import_database,
    search_database,
    optimize_database,
    reset_database,
)

# Script Metadata
SCRIPT_VERSION = "3.0"
SCRIPT_DATE = "2025-07-06"

# Define variables
SCRIPTS_FOLDER = "MediaHub"
ENV_FILE = ".env"

# Setup logging
logging.basicConfig(
    filename="script.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Determine the Python command based on the OS
python_command = "python" if os.name == "nt" else "python3"


class MainMenu(Screen):
    def compose(self) -> ComposeResult:
        yield Header(name="CineSync")
        yield Footer()
        with Container(classes="main-menu"):
            yield Static("[bold #03AC13]CineSync[/bold #03AC13]", classes="title")
            yield Static("Your personal media organizer.", classes="subtitle")
            yield Static(
                "\n[italic]Organize your media library with ease.[/italic]\n",
                classes="tagline",
            )
            with Vertical(classes="main-menu-buttons"):
                yield Button(
                    "Sort Library", id="sort", variant="primary", classes="menu-button"
                )
                yield Button(
                    "Edit .env file",
                    id="edit_env",
                    variant="primary",
                    classes="menu-button",
                )
                yield Button(
                    "Database Management",
                    id="db_manage",
                    variant="primary",
                    classes="menu-button",
                )
                yield Button("Exit", id="exit", variant="error", classes="menu-button")
            yield Static(
                "\n[dim]Version: 3.0 | Date: 2025-07-06[/dim]", classes="version-info"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sort":
            self.app.push_screen(FileSelectionScreen())
        elif event.button.id == "edit_env":
            self.app.run_subprocess(f"nano {ENV_FILE}")
        elif event.button.id == "db_manage":
            self.app.push_screen(DatabaseMenu())
        elif event.button.id == "exit":
            self.app.exit()


class DatabaseMenu(Screen):
    def compose(self) -> ComposeResult:
        yield Header(name="Database Management")
        yield Footer()
        yield Container(
            Button("View Database Status", id="db_status", variant="primary"),
            Button("Optimize Database", id="db_optimize", variant="primary"),
            Button("Verify Database Integrity", id="db_verify", variant="primary"),
            Button("Vacuum Database", id="db_vacuum", variant="primary"),
            Button("Export Database", id="db_export", variant="primary"),
            Button("Import Database", id="db_import", variant="primary"),
            Button("Search Database", id="db_search", variant="primary"),
            Button("Reset Database", id="db_reset", variant="warning"),
            Button("Back to Main Menu", id="back", variant="default"),
            classes="db-menu",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "db_status":
            stats = get_database_stats()
            self.app.push_screen(ResultScreen(Pretty(stats)))
        elif event.button.id == "db_optimize":
            optimize_database()
            self.app.push_screen(ResultScreen(Static("Database optimized.")))
        elif event.button.id == "db_verify":
            verify_database_integrity()
            self.app.push_screen(ResultScreen(Static("Database integrity verified.")))
        elif event.button.id == "db_vacuum":
            vacuum_database()
            self.app.push_screen(ResultScreen(Static("Database vacuumed.")))
        elif event.button.id == "db_export":
            # This would need a way to get user input, which is more complex in textual
            pass
        elif event.button.id == "db_import":
            # This would need a way to get user input
            pass
        elif event.button.id == "db_search":
            # This would need a way to get user input
            pass
        elif event.button.id == "db_reset":
            # This would need a confirmation dialog
            pass
        elif event.button.id == "back":
            self.app.pop_screen()


class FileSelectionScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(name="File Selection")
        yield Footer()
        with Container(classes="file-selection-container"):
            yield Static(
                "Select files and folders to sort:", classes="file-selection-prompt"
            )
            with Horizontal():
                yield Input(
                    placeholder="Search folders/files...",
                    id="search_bar",
                    classes="file-selection-search",
                )
                yield Button("Apply", id="search_apply", classes="file-selection-apply")
            yield Tree("Sources", id="file_tree", classes="file-selection-tree")
            yield Button(
                "Sort Selected",
                id="sort_selected",
                variant="success",
                classes="file-selection-button",
            )

    def on_mount(self) -> None:
        src_dirs, _ = get_directories()
        tree = self.query_one(Tree)
        tree.clear()
        self.selected_paths = set()
        # Add only top-level source directories
        for src_dir in src_dirs:
            self._add_full_node(tree.root, src_dir)
        tree.root.expand()
        self.query_one("#search_apply").on_click = self.on_search_apply

    def _add_full_node(self, parent, path):
        import os

        basename = os.path.basename(path)
        node = parent.add(
            f"[📁] [ ] {basename}",
            data={"path": path, "type": "dir", "checked": False},
        )
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.is_dir():
                        self._add_full_node(node, entry.path)
                    elif entry.is_file():
                        node.add(
                            f"[📄] [ ] {entry.name}",
                            data={"path": entry.path, "type": "file", "checked": False},
                        )
        except Exception as e:
            print(f"EXCEPTION in _add_full_node: {e}")

    def on_tree_node_selected(self, event):
        node = event.node
        data = node.data
        if data:
            checked = not data.get("checked", False)
            data["checked"] = checked
            label_str = str(node.label)
            if checked:
                node.set_label(label_str.replace("[ ]", "[x]", 1))
                self.selected_paths.add(data["path"])
            else:
                node.set_label(label_str.replace("[x]", "[ ]", 1))
                self.selected_paths.discard(data["path"])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sort_selected":
            if self.selected_paths:
                self.app.push_screen(LogScreen(list(self.selected_paths)))

    def on_search_apply(self, event):
        value = self.query_one("#search_bar").value.strip()
        self._rebuild_tree_with_search(value)

    def _rebuild_tree_with_search(self, query):
        # Rebuild the tree, only showing matching nodes (and their parents)
        src_dirs, _ = get_directories()
        tree = self.query_one(Tree)
        tree.clear()
        if not query:
            for src_dir in src_dirs:
                self._add_full_node(tree.root, src_dir)
            tree.root.expand()
            return
        # If query, do a shallow search (top-level only, for performance)
        for src_dir in src_dirs:
            import os

            basename = os.path.basename(src_dir.rstrip("/"))
            if query.lower() in basename.lower():
                self._add_full_node(tree.root, src_dir)
            else:
                # Search immediate children
                try:
                    for name in sorted(os.listdir(src_dir)):
                        if query.lower() in name.lower():
                            full_path = os.path.join(src_dir, name)
                            if os.path.isdir(full_path):
                                self._add_full_node(tree.root, full_path)
                            else:
                                tree.root.add(
                                    f"[📄] [ ] {name}",
                                    data={
                                        "path": full_path,
                                        "type": "file",
                                        "checked": False,
                                    },
                                )
                except Exception:
                    pass
        tree.root.expand()


class LogScreen(Screen):
    def __init__(self, paths_to_sort):
        super().__init__()
        self.paths_to_sort = paths_to_sort

    def compose(self) -> ComposeResult:
        yield Header(name="Sorting Logs")
        yield Footer()
        with Container(classes="log-screen-container"):
            yield RichLog(id="log_view", wrap=True)
            yield Button(
                "Back", id="back", variant="default", classes="log-screen-button"
            )

    def on_mount(self) -> None:
        self.run_sorting()

    def run_sorting(self):
        log_view = self.query_one(RichLog)
        _, dest_dir = get_directories()

        def log_to_widget(message, level="INFO"):
            if level == "INFO":
                log_view.write(f"[green]INFO[/green]: {message}")
            elif level == "WARNING":
                log_view.write(f"[yellow]WARNING[/yellow]: {message}")
            elif level == "ERROR":
                log_view.write(f"[red]ERROR[/red]: {message}")
            else:
                log_view.write(f"[white]{level}[/white]: {message}")

        create_symlinks(
            self.paths_to_sort, dest_dir, auto_select=True, console_log=log_to_widget
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()


class ResultScreen(Screen):
    def __init__(self, content):
        super().__init__()
        self.content = content

    def compose(self) -> ComposeResult:
        yield Header(name="Result")
        yield Footer()
        yield self.content
        yield Button("Back", id="back", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()


class CineSync(App):
    CSS_PATH = "cinesync.css"
    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.push_screen(MainMenu())

    def action_toggle_dark(self) -> None:
        self.dark = not self.dark


def main():
    parser = argparse.ArgumentParser(description="CineSync TUI")
    parser.add_argument(
        "--sort-all", action="store_true", help="Sort all files without prompting"
    )
    args = parser.parse_args()

    if args.sort_all:
        src_dirs, dest_dir = get_directories()
        if not src_dirs:
            print("[bold red]Error: SOURCE_DIR not set in .env file.[/bold red]")
            return
        for src_dir in src_dirs:
            create_symlinks([src_dir], dest_dir, auto_select=True)
        print("[bold green]Sorting complete![/bold green]")
        return

    app = CineSync()
    app.run()


if __name__ == "__main__":
    main()
