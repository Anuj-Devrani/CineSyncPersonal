import os
import platform
import re
import time
import traceback
import sqlite3
from threading import Thread
from queue import Queue, Empty
from threading import Thread, Event
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
from threading import Event
from MediaHub.processors.movie_processor import process_movie
from MediaHub.processors.show_processor import process_show
from MediaHub.utils.logging_utils import log_message
from MediaHub.utils.file_utils import build_dest_index, get_anime_patterns, is_junk_file
from MediaHub.monitor.symlink_cleanup import run_symlink_cleanup
from MediaHub.config.config import *
from MediaHub.processors.db_utils import *
from MediaHub.utils.plex_utils import *
from MediaHub.processors.process_db import *

error_event = Event()
log_imported_db = False
db_initialized = False


def process_file(args, processed_files_log, force=False, console=None):
    (
        src_file,
        root,
        file,
        dest_dir,
        actual_dir,
        tmdb_folder_id_enabled,
        rename_enabled,
        auto_select,
        dest_index,
        tmdb_id,
        imdb_id,
        tvdb_id,
        force_show,
        force_movie,
        season_number,
        episode_number,
        force_extra,
    ) = args

    if error_event.is_set():
        return

    # Normalize path
    src_file = os.path.normpath(src_file)

    # Skip if not a known file type
    if not get_known_types(file):
        if console:
            console(f"Skipping unsupported file type: {file}")
        else:
            log_message(f"Skipping unsupported file type: {file}", level="INFO")
        return

    skip_extras_folder = is_skip_extras_folder_enabled()

    # Handle force mode
    if force:
        existing_symlink_path = get_existing_symlink_info(src_file)
        if existing_symlink_path:
            if console:
                console(
                    f"Force mode: Found existing symlink at {existing_symlink_path}"
                )
            else:
                log_message(
                    f"Force mode: Found existing symlink at {existing_symlink_path}",
                    level="DEBUG",
                )
            parent_dir = os.path.dirname(existing_symlink_path)
            parent_parent_dir = os.path.dirname(parent_dir)
            os.remove(existing_symlink_path)
            if console:
                console(f"Force mode: Initiating reprocessing of {file}")
            else:
                log_message(
                    f"Force mode: Initiating reprocessing of {file}", level="INFO"
                )

            # Delete if parent directory is empty
            try:
                if not os.listdir(parent_dir):
                    if console:
                        console(f"Deleting empty directory: {parent_dir}")
                    else:
                        log_message(
                            f"Deleting empty directory: {parent_dir}", level="INFO"
                        )
                    os.rmdir(parent_dir)

                    if not os.listdir(parent_parent_dir):
                        if console:
                            console(f"Deleting empty directory: {parent_parent_dir}")
                        else:
                            log_message(
                                f"Deleting empty directory: {parent_parent_dir}",
                                level="INFO",
                            )
                        os.rmdir(parent_parent_dir)
            except OSError as e:
                if console:
                    console(f"Error deleting directory: {e}")
                else:
                    log_message(f"Error deleting directory: {e}", level="WARNING")

    existing_dest_path = get_destination_path(src_file)
    if existing_dest_path and not force:
        if not os.path.exists(existing_dest_path):
            dir_path = os.path.dirname(existing_dest_path)
            if os.path.exists(dir_path):
                for filename in os.listdir(dir_path):
                    potential_new_path = os.path.join(dir_path, filename)
                    if (
                        os.path.islink(potential_new_path)
                        and os.readlink(potential_new_path) == src_file
                    ):
                        if console:
                            console(
                                f"Detected renamed file: {existing_dest_path} -> {potential_new_path}"
                            )
                        else:
                            log_message(
                                f"Detected renamed file: {existing_dest_path} -> {potential_new_path}",
                                level="INFO",
                            )
                        update_renamed_file(existing_dest_path, potential_new_path)
                        return

            if console:
                console(f"Destination file missing. Re-processing: {src_file}")
            else:
                log_message(
                    f"Destination file missing. Re-processing: {src_file}", level="INFO"
                )
        else:
            if console:
                console(
                    f"File already processed. Source: {src_file}, Existing destination: {existing_dest_path}"
                )
            else:
                log_message(
                    f"File already processed. Source: {src_file}, Existing destination: {existing_dest_path}",
                    level="INFO",
                )
            return

    # Check if a symlink already exists
    existing_symlink = next(
        (
            full_dest_file
            for full_dest_file in dest_index
            if os.path.islink(full_dest_file)
            and os.readlink(full_dest_file) == src_file
        ),
        None,
    )

    if existing_symlink and not force:
        if console:
            console(f"Symlink already exists for {os.path.basename(file)}")
        else:
            log_message(
                f"Symlink already exists for {os.path.basename(file)}", level="INFO"
            )
        save_processed_file(src_file, existing_symlink, tmdb_id)
        return

    # Show detection logic
    is_show = False
    is_anime_show = False
    episode_match = None

    # Skip hash filenames unless they have valid media patterns
    hash_pattern = re.compile(r"^[a-f0-9]{32}(\.[^.]+$|\[.+?\]\.)", re.IGNORECASE)
    is_hash_name = hash_pattern.search(file) is not None

    if is_hash_name and not tmdb_id and not imdb_id:
        if console:
            console(f"Skipping file with hash lacking media identifiers: {file}")
        else:
            log_message(
                f"Skipping file with hash lacking media identifiers: {file}",
                level="INFO",
            )
        return

    if force_show:
        is_show = True
        if console:
            console(f"Processing as show based on Force Show flag: {file}")
        else:
            log_message(
                f"Processing as show based on Force Show flag: {file}", level="INFO"
            )
    elif force_movie:
        is_show = False
        if console:
            console(f"Processing as movie based on Force Movie flag: {file}")
        else:
            log_message(
                f"Processing as movie based on Force Movie flag: {file}", level="INFO"
            )
    else:
        episode_match = re.search(
            r"(.*?)(S\d{1,2}\.?E\d{2}|S\d{1,2}\s*\d{2}|S\d{2}E\d{2}|S\d{2}e\d{2}|(?<!\d{3})\b[1-9][0-9]?x[0-9]{1,2}\b(?!\d{3})|[0-9]+e[0-9]+|\bep\.?\s*\d{1,2}\b|\bEp\.?\s*\d{1,2}\b|\bEP\.?\s*\d{1,2}\b|S\d{2}\sE\d{2}|MINI[- ]SERIES|MINISERIES|\s-\s(?!1080p|720p|480p|2160p|\d+Kbps|\d{4}|\d+bit)\d{2,3}(?!Kbps)|\s-(?!1080p|720p|480p|2160p|\d+Kbps|\d{4}|\d+bit)\d{2,3}(?!Kbps)|\s-\s*(?!1080p|720p|480p|2160p|\d+Kbps|\d{4}|\d+bit)\d{2,3}(?!Kbps)|[Ee]pisode\s*\d{2}|[Ee]p\s*\d{2}|Season_-\d{2}|\bSeason\d+\b|\bE\d+\b|series\.\d+\.\d+of\d+|Episode\s+(\d+)\s+(.*?)\.(\w+)|\b\d{2}x\d{2}\b)",
            file,
            re.IGNORECASE,
        )
        mini_series_match = re.search(
            r"(MINI[- ]SERIES|MINISERIES)", file, re.IGNORECASE
        )
        anime_episode_pattern = re.compile(r"\s-\s\d{2,3}\s|\d{2,3}v\d+", re.IGNORECASE)
        anime_patterns = get_anime_patterns()
        season_pattern = re.compile(r"\b(s\d{2})\b", re.IGNORECASE)

        # Check file path and name for show patterns
        if season_pattern.search(src_file):
            is_show = True
            if console:
                console(f"Processing as show based on directory structure: {src_file}")
            else:
                log_message(
                    f"Processing as show based on directory structure: {src_file}",
                    level="DEBUG",
                )
        elif episode_match or mini_series_match:
            is_show = True
            if console:
                console(f"Processing as show based on file pattern: {src_file}")
            else:
                log_message(
                    f"Processing as show based on file pattern: {src_file}",
                    level="DEBUG",
                )
        elif anime_episode_pattern.search(file) or anime_patterns.search(file):
            is_anime_show = True
            if console:
                console(f"Processing as show based on anime pattern: {src_file}")
            else:
                log_message(
                    f"Processing as show based on anime pattern: {src_file}",
                    level="DEBUG",
                )

    # Check if the file should be considered an junk based on size
    if is_junk_file(file, src_file):
        if console:
            console(f"Skipping Junk files: {file} based on size")
        else:
            log_message(f"Skipping Junk files: {file} based on size", level="DEBUG")
        return

    # Determine whether to process as show or movie
    if is_show or is_anime_show:
        dest_file, tmdb_id, season_number, is_extra = process_show(
            src_file,
            root,
            file,
            dest_dir,
            actual_dir,
            tmdb_folder_id_enabled,
            rename_enabled,
            auto_select,
            dest_index,
            episode_match,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            season_number=season_number,
            episode_number=episode_number,
            is_anime_show=is_anime_show,
            force_extra=force_extra,
        )

        # Skip symlink creation for extras unless skipped from env or force_extra is enabled
        if is_extra and not force_extra and is_skip_extras_folder_enabled():
            if console:
                console(f"Skipping symlink creation for extra file: {file}")
            else:
                log_message(
                    f"Skipping symlink creation for extra file: {file}", level="INFO"
                )
            return
    else:
        dest_file, tmdb_id = process_movie(
            src_file,
            root,
            file,
            dest_dir,
            actual_dir,
            tmdb_folder_id_enabled,
            rename_enabled,
            auto_select,
            dest_index,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
        )

    if dest_file is None:
        if console:
            console(f"Destination file path is None for {file}. Skipping.")
        else:
            log_message(
                f"Destination file path is None for {file}. Skipping.", level="WARNING"
            )
        return

    os.makedirs(os.path.dirname(dest_file), exist_ok=True)

    # Check if symlink already exists
    if os.path.islink(dest_file):
        existing_src = os.readlink(dest_file)
        if existing_src == src_file:
            if console:
                console(
                    f"Symlink already exists and is correct: {dest_file} -> {src_file}"
                )
            else:
                log_message(
                    f"Symlink already exists and is correct: {dest_file} -> {src_file}",
                    level="INFO",
                )
            save_processed_file(src_file, dest_file, tmdb_id)
            return
        else:
            if console:
                console(
                    f"Updating existing symlink: {dest_file} -> {src_file} (was: {existing_src})"
                )
            else:
                log_message(
                    f"Updating existing symlink: {dest_file} -> {src_file} (was: {existing_src})",
                    level="INFO",
                )
            os.remove(dest_file)

    if os.path.exists(dest_file) and not os.path.islink(dest_file):
        if console:
            console(
                f"File already exists at destination: {os.path.basename(dest_file)}"
            )
        else:
            log_message(
                f"File already exists at destination: {os.path.basename(dest_file)}",
                level="INFO",
            )
        return

    # Create symlink
    try:
        os.symlink(src_file, dest_file)
        if console:
            console(f"Created symlink: {dest_file} -> {src_file}")
        else:
            log_message(f"Created symlink: {dest_file} -> {src_file}", level="INFO")
        save_processed_file(src_file, dest_file, tmdb_id, season_number)

        if plex_update() and plex_token():
            update_plex_after_symlink(dest_file)

        return (dest_file, True, src_file)

    except FileExistsError:
        if console:
            console(f"File already exists: {dest_file}. Skipping symlink creation.")
        else:
            log_message(
                f"File already exists: {dest_file}. Skipping symlink creation.",
                level="WARNING",
            )
    except OSError as e:
        if console:
            console(f"Error creating symlink for {src_file}: {e}")
        else:
            log_message(f"Error creating symlink for {src_file}: {e}", level="ERROR")
    except Exception as e:
        error_message = f"Task failed with exception: {e}"
        if console:
            console(error_message)
        else:
            log_message(error_message, level="ERROR")

    return None


def create_symlinks(
    src_dirs,
    dest_dir,
    auto_select=False,
    single_path=None,
    force=False,
    mode="create",
    tmdb_id=None,
    imdb_id=None,
    tvdb_id=None,
    force_show=False,
    force_movie=False,
    season_number=None,
    episode_number=None,
    force_extra=False,
    console_log=None,
):
    global log_imported_db

    os.makedirs(dest_dir, exist_ok=True)
    tmdb_folder_id_enabled = is_tmdb_folder_id_enabled()
    rename_enabled = is_rename_enabled()
    skip_extras_folder = is_skip_extras_folder_enabled()
    imdb_structure_id_enabled = is_imdb_folder_id_enabled()

    # Initialize database if in monitor mode
    if mode == "monitor" and not os.path.exists(PROCESS_DB):
        initialize_file_database()

    # Use single_path if provided
    if single_path:
        src_dirs = [single_path]

    # Load the record of processed files
    processed_files_log = load_processed_files()

    # Initialize counters for progress tracking
    total_files = 0
    processed_files = 0

    # First pass to count total files for progress tracking
    if auto_select:
        for src_dir in src_dirs:
            if os.path.isfile(src_dir):
                total_files += 1
            else:
                for root, _, files in os.walk(src_dir):
                    total_files += len([f for f in files if get_known_types(f)])

    log_message(f"Starting to process {total_files} files...", level="INFO")
    if console_log:
        console_log(f"Starting to process {total_files} files...")

    if auto_select:
        # Use thread pool for parallel processing when auto-select is enabled
        tasks = []
        with ThreadPoolExecutor(max_workers=cpu_count()) as executor:
            for src_dir in src_dirs:
                if os.path.isfile(src_dir):
                    src_file = src_dir
                    root = os.path.dirname(src_file)
                    file = os.path.basename(src_file)
                    actual_dir = os.path.basename(root)

                    # Get appropriate destination index based on mode
                    dest_index = (
                        get_dest_index_from_db()
                        if mode == "monitor"
                        else build_dest_index(dest_dir)
                    )

                    args = (
                        src_file,
                        root,
                        file,
                        dest_dir,
                        actual_dir,
                        tmdb_folder_id_enabled,
                        rename_enabled,
                        auto_select,
                        dest_index,
                        tmdb_id,
                        imdb_id,
                        tvdb_id,
                        force_show,
                        force_movie,
                        season_number,
                        episode_number,
                        force_extra,
                    )
                    tasks.append(
                        executor.submit(
                            process_file, args, processed_files_log, force, console_log
                        )
                    )
                else:
                    # Handle directory
                    base_src_dir = os.path.normpath(src_dir)
                    if console_log:
                        console_log(f"Scanning source directory: {base_src_dir}")
                    else:
                        log_message(
                            f"Scanning source directory: {base_src_dir}", level="INFO"
                        )

                    # Get appropriate destination index based on mode
                    dest_index = (
                        get_dest_index_from_db()
                        if mode == "monitor"
                        else build_dest_index(dest_dir)
                    )

                    for root, _, files in os.walk(base_src_dir):
                        # Calculate the relative path from the source directory
                        rel_path = os.path.relpath(root, base_src_dir)
                        if rel_path == ".":
                            actual_dir = os.path.basename(base_src_dir)
                        else:
                            actual_dir = os.path.join(
                                os.path.basename(base_src_dir), rel_path
                            )

                        for file in files:
                            if error_event.is_set():
                                if console_log:
                                    console_log(
                                        "Stopping further processing due to an earlier error."
                                    )
                                else:
                                    log_message(
                                        "Stopping further processing due to an earlier error.",
                                        level="WARNING",
                                    )
                                return

                            src_file = os.path.join(root, file)

                            if (
                                mode == "create"
                                and src_file in processed_files_log
                                and not force
                            ):
                                if console_log:
                                    console_log(
                                        f"Skipping already processed file: {file}"
                                    )
                                continue

                            processed_files += 1
                            if console_log:
                                progress = (processed_files / total_files) * 100
                                console_log(
                                    f"Processing file {processed_files}/{total_files} ({progress:.1f}%): {file}"
                                )
                            else:
                                log_message(
                                    f"Processing file {processed_files}/{total_files}: {file}",
                                    level="INFO",
                                )

                            args = (
                                src_file,
                                root,
                                file,
                                dest_dir,
                                actual_dir,
                                tmdb_folder_id_enabled,
                                rename_enabled,
                                auto_select,
                                dest_index,
                                tmdb_id,
                                imdb_id,
                                tvdb_id,
                                force_show,
                                force_movie,
                                season_number,
                                episode_number,
                                force_extra,
                            )
                            tasks.append(
                                executor.submit(
                                    process_file,
                                    args,
                                    processed_files_log,
                                    force,
                                    console_log,
                                )
                            )

            # Process completed tasks
            for task in as_completed(tasks):
                if error_event.is_set():
                    error_msg = (
                        "Error detected during task execution. Stopping all tasks."
                    )
                    if console_log:
                        console_log(error_msg, level="ERROR")
                    else:
                        log_message(error_msg, level="ERROR")
                    return

                try:
                    result = task.result()
                    if result and isinstance(result, tuple) and len(result) == 3:
                        dest_file, is_symlink, target_path = result
                        if mode == "monitor":
                            update_single_file_index(dest_file, is_symlink, target_path)

                        processed_files += 1
                        if console_log:
                            progress = (processed_files / total_files) * 100
                            console_log(
                                f"Processed {processed_files}/{total_files} ({progress:.1f}%)"
                            )

                        log_message(
                            f"Successfully processed: {os.path.basename(dest_file)}",
                            level="INFO",
                        )
                        if is_symlink:
                            log_message(
                                f"Created symlink: {dest_file} -> {target_path}",
                                level="DEBUG",
                            )

                except Exception as e:
                    error_msg = f"Error processing task: {str(e)}"
                    if console_log:
                        console_log(error_msg, level="ERROR")
                    else:
                        log_message(error_msg, level="ERROR")
                    error_event.set()
                    continue

            # Final completion message
            completion_msg = (
                f"Processing completed. Successfully processed {processed_files} files."
            )
            if console_log:
                console_log(completion_msg, level="SUCCESS")
            log_message(completion_msg, level="INFO")
    else:
        # Process sequentially when auto-select is disabled
        for src_dir in src_dirs:
            if error_event.is_set():
                if console_log:
                    console_log("Stopping further processing due to an earlier error.")
                else:
                    log_message(
                        "Stopping further processing due to an earlier error.",
                        level="WARNING",
                    )
                return

            try:
                if os.path.isfile(src_dir):
                    src_file = src_dir
                    root = os.path.dirname(src_file)
                    file = os.path.basename(src_file)
                    actual_dir = os.path.basename(root)

                    # Get appropriate destination index based on mode
                    dest_index = (
                        get_dest_index_from_db()
                        if mode == "monitor"
                        else build_dest_index(dest_dir)
                    )

                    args = (
                        src_file,
                        root,
                        file,
                        dest_dir,
                        actual_dir,
                        tmdb_folder_id_enabled,
                        rename_enabled,
                        auto_select,
                        dest_index,
                        tmdb_id,
                        imdb_id,
                        tvdb_id,
                        force_show,
                        force_movie,
                        season_number,
                        episode_number,
                        force_extra,
                    )
                    result = process_file(args, processed_files_log, force, console_log)

                    if result and isinstance(result, tuple) and len(result) == 3:
                        dest_file, is_symlink, target_path = result
                        if mode == "monitor":
                            update_single_file_index(dest_file, is_symlink, target_path)
                else:
                    # Handle directory
                    base_src_dir = os.path.normpath(src_dir)
                    if console_log:
                        console_log(f"Scanning source directory: {base_src_dir}")
                    else:
                        log_message(
                            f"Scanning source directory: {base_src_dir}", level="INFO"
                        )

                    # Get appropriate destination index based on mode
                    dest_index = (
                        get_dest_index_from_db()
                        if mode == "monitor"
                        else build_dest_index(dest_dir)
                    )

                    for root, _, files in os.walk(base_src_dir):
                        # Calculate the relative path from the source directory
                        rel_path = os.path.relpath(root, base_src_dir)
                        if rel_path == ".":
                            actual_dir = os.path.basename(base_src_dir)
                        else:
                            actual_dir = os.path.join(
                                os.path.basename(base_src_dir), rel_path
                            )

                        for file in files:
                            if error_event.is_set():
                                if console_log:
                                    console_log(
                                        "Stopping further processing due to an earlier error."
                                    )
                                else:
                                    log_message(
                                        "Stopping further processing due to an earlier error.",
                                        level="WARNING",
                                    )
                                return

                            src_file = os.path.join(root, file)

                            if (
                                mode == "create"
                                and src_file in processed_files_log
                                and not force
                            ):
                                continue

                            args = (
                                src_file,
                                root,
                                file,
                                dest_dir,
                                actual_dir,
                                tmdb_folder_id_enabled,
                                rename_enabled,
                                auto_select,
                                dest_index,
                                tmdb_id,
                                imdb_id,
                                tvdb_id,
                                force_show,
                                force_movie,
                                season_number,
                                episode_number,
                                force_extra,
                            )
                            result = process_file(
                                args, processed_files_log, force, console_log
                            )

                            if (
                                result
                                and isinstance(result, tuple)
                                and len(result) == 3
                            ):
                                dest_file, is_symlink, target_path = result
                                if mode == "monitor":
                                    update_single_file_index(
                                        dest_file, is_symlink, target_path
                                    )
            except Exception as e:
                if console_log:
                    console_log(f"Error processing directory {src_dir}: {str(e)}")
                else:
                    log_message(
                        f"Error processing directory {src_dir}: {str(e)}", level="ERROR"
                    )
