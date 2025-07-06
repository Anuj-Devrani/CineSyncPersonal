
import os
import sys
import subprocess
import getpass
import logging
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.tree import Tree
from rich.live import Live
from rich.filesize import decimal
from rich.markup import escape
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from MediaHub.processors.symlink_creator import create_symlinks
from MediaHub.config.config import get_directories

# Append the parent directory to the system path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local imports from MediaHub
from MediaHub.utils.logging_utils import log_message
from MediaHub.processors.db_utils import get_database_stats, vacuum_database, verify_database_integrity, export_database, import_database, search_database, optimize_database, reset_database

# Script Metadata
SCRIPT_VERSION = "3.0"
SCRIPT_DATE = "2025-07-06"

# Define variables
SCRIPTS_FOLDER = "MediaHub"
ENV_FILE = ".env"

# Setup logging
logging.basicConfig(filename='script.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Determine the Python command based on the OS
python_command = 'python' if os.name == 'nt' else 'python3'

console = Console()

def print_banner():
    """Prints the banner using rich."""
    banner = """
    a88888b. oo                   .d88888b
   d8'   `88                      88.    "'
   88        dP 88d888b. .d8888b. `Y88888b. dP    dP 88d888b. .d8888b.
   88        88 88'  `88 88ooood8       `8b 88    88 88'  `88 88'  `"`
   Y8.   .88 88 88    88 88.  ... d8'   .8P 88.  .88 88    88 88.  ...
    Y88888P' dP dP    dP `88888P'  Y88888P  `8888P88 dP    dP `88888P'
                                                 .88
                                             d8888P
    """
    console.print(Panel.fit(banner, style="bold blue"))
    console.print(f"Version {SCRIPT_VERSION} - Last updated on {SCRIPT_DATE}", style="bold green")

def greet_user():
    """Greets the user."""
    username = getpass.getuser()
    console.print(f"Welcome, [bold green]{username}[/bold green]!")

def get_file_selection(source_dirs):
    """Allows the user to select files and folders to sort."""
    selected_files = []
    tree = Tree("Select files and folders to sort", guide_style="bold bright_blue")

    for src_dir in source_dirs:
        branch = tree.add(f"[bold green]:open_file_folder: [link file://{src_dir}]{src_dir}")
        try:
            for entry in os.scandir(src_dir):
                if entry.is_dir():
                    branch.add(f"[bold blue]:file_folder: {entry.name}")
                else:
                    branch.add(f"[green]:page_facing_up: {entry.name}")
        except FileNotFoundError:
            console.print(f"[bold red]Error: Directory not found: {src_dir}[/bold red]")
            continue

    with Live(tree, console=console, screen=False, refresh_per_second=10) as live:
        while True:
            console.print("Enter a path to select/deselect, 's' to start sorting, or 'q' to quit.")
            user_input = Prompt.ask("Selection")

            if user_input.lower() == 'q':
                return []
            if user_input.lower() == 's':
                return selected_files

            path_to_toggle = user_input.strip()
            if path_to_toggle in selected_files:
                selected_files.remove(path_to_toggle)
                console.print(f"[yellow]Deselected:[/] {path_to_toggle}")
            else:
                selected_files.append(path_to_toggle)
                console.print(f"[green]Selected:[/] {path_to_toggle}")

def main():
    """Main function for the TUI."""
    parser = argparse.ArgumentParser(description="CineSync TUI")
    parser.add_argument("--sort-all", action="store_true", help="Sort all files without prompting")
    args = parser.parse_args()

    if args.sort_all:
        src_dirs, dest_dir = get_directories()
        if not src_dirs:
            console.print("[bold red]Error: SOURCE_DIR not set in .env file.[/bold red]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        ) as progress:
            task = progress.add_task("[green]Sorting files...", total=len(src_dirs))
            for src_dir in src_dirs:
                create_symlinks([src_dir], dest_dir, auto_select=True, console=console)
                progress.update(task, advance=1)
        console.print("[bold green]Sorting complete![/bold green]")
        return

    while True:
        console.clear()
        print_banner()
        greet_user()

        console.print("\n[bold blue]Main Menu:[/bold blue]")
        console.print("1) Sort Library")
        console.print("2) Edit .env file")
        console.print("3) Database Management")
        console.print("4) Exit")

        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4"], default="1")

        if choice == '1':
            src_dirs, dest_dir = get_directories()
            if not src_dirs:
                console.print("[bold red]Error: SOURCE_DIR not set in .env file.[/bold red]")
                continue

            selected_paths = get_file_selection(src_dirs)
            if selected_paths:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                ) as progress:
                    task = progress.add_task("[green]Sorting files...", total=len(selected_paths))
                    for path in selected_paths:
                        create_symlinks(src_dirs, dest_dir, single_path=path, auto_select=True)
                        progress.update(task, advance=1)
                console.print("[bold green]Sorting complete![/bold green]")
        elif choice == '2':
            edit_env_file()
        elif choice == '3':
            database_management()
        elif choice == '4':
            console.print("Exiting. Goodbye!")
            break

def edit_env_file():
    """Function to edit the .env file."""
    try:
        if os.path.exists(ENV_FILE):
            subprocess.run([python_command, '-m', 'nano', ENV_FILE], check=True)
            console.print("\n.env file editing completed.")
        else:
            console.print("The .env file does not exist. Creating a new one.")
            with open(ENV_FILE, 'w') as f:
                pass
            subprocess.run([python_command, '-m', 'nano', ENV_FILE], check=True)
            console.print("\n.env file created and edited.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error editing .env file: {e}")
        console.print("[bold red]Error editing .env file. Check the log for details.[/bold red]")
    input("Press Enter to return to the main menu...")

def database_management():
    """Function to execute Database Management."""
    while True:
        console.clear()
        print_banner()
        console.print("\n[bold blue]Database Management Options:[/bold blue]")
        console.print("1) View Database Status")
        console.print("2) Optimize Database")
        console.print("3) Verify Database Integrity")
        console.print("4) Vacuum Database")
        console.print("5) Export Database")
        console.print("6) Import Database")
        console.print("7) Search Database")
        console.print("8) Reset Database")
        console.print("9) Back to Main Menu")

        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"], default="9")

        try:
            if choice == '1':
                stats = get_database_stats()
                if stats:
                    console.print("\n[bold green]Database Statistics:[/bold green]")
                    console.print(f"Total Records: {stats['total_records']}")
                    console.print(f"Archived Records: {stats['archived_records']}")
                    console.print(f"Main DB Size: {stats['main_db_size']:.2f} MB")
                    console.print(f"Archive DB Size: {stats['archive_db_size']:.2f} MB")
            elif choice == '2':
                optimize_database()
            elif choice == '3':
                verify_database_integrity()
            elif choice == '4':
                vacuum_database()
            elif choice == '5':
                filename = Prompt.ask("Enter export filename (CSV)")
                export_database(filename)
            elif choice == '6':
                filename = Prompt.ask("Enter import filename (CSV)")
                import_database(filename)
            elif choice == '7':
                pattern = Prompt.ask("Enter search pattern")
                search_database(pattern)
            elif choice == '8':
                if Confirm.ask("Are you sure you want to reset the database? This will delete all entries."):
                    reset_database()
            elif choice == '9':
                break
            else:
                console.print("[bold red]Invalid option. Please select again.[/bold red]")

            Prompt.ask("\nPress Enter to continue...")
        except Exception as e:
            logging.error(f"Error in database management: {e}")
            console.print(f"[bold red]An error occurred: {e}[/bold red]")
            Prompt.ask("\nPress Enter to continue...")

if __name__ == "__main__":
    main()
