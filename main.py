import logging
import os
import sys
from pathlib import Path
import click
import platformdirs

# Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog, QMainWindow
from PySide6.QtGui import QIcon

from app import PlaylistSaver
from constant import PROGRAM_DIR
from pl_lib import messagebox

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)



def show_error(message):
    app = QApplication(sys.argv)
    app.setApplicationName("Launcher Bootstrapper")
    window = QMainWindow()
    messagebox.error(window, "Error", message)
    app.exit()

@click.command()
@click.argument("url_schema", default=None, required=False)
@click.option("--cookie-file", "-cf", required=False, default=None, help="Cookies file",
              type=click.Path(exists=True))
@click.option("--work-dir", "-wd", required=False, default=None, help="Custom working directory",
              type=click.Path(exists=True))
def main(url_schema, cookie_file: str, work_dir: str | None):
    icon_path = PROGRAM_DIR / "assets" / "icon.ico"

    # Default working directory
    if not work_dir:
        try:
            # Use user application data directory
            work_dir = work_dir = platformdirs.user_data_dir("PlaylistSaver", appauthor=False, roaming=True)
            os.makedirs(work_dir, exist_ok=True)
        except Exception as e:
            logger.warning("Failed to get application directory: %s", e)
            show_error("Unable to get application directory. Try specifying 'work_dir' argument.")
            sys.exit(-1)

    if work_dir is None:
        app = QApplication(sys.argv)

        if icon_path.exists():
            app.setWindowIcon(QIcon(icon_path.as_posix()))

        folder = QFileDialog.getExistingDirectory(
            None, "Select a folder as the new working directory"
        )

        if folder:
            work_dir = Path(folder)
        else:
            QMessageBox.warning(
                None,
                "Warning",
                "PlaylistSaver requires a working directory to store files. Please specify a working directory"
                " using argument \"--work-dir\".",
            )
            sys.exit(1)

        app.shutdown()

    logger.info(f"Working directory: {work_dir}")

    # Create app and set icon
    app = PlaylistSaver(
        work_dir,
        url_schema,
        cookie_file=cookie_file,
    )

    if icon_path.exists():
        app.setWindowIcon(QIcon(icon_path.as_posix()))
    else:
        logger.warning("Application icon not found.")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
