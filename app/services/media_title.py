"""Display labels without release tags; never change identity-matching normalization."""
import re
from pathlib import Path

from app.services.media_analysis import EPISODE_SXE, EPISODE_X, YEAR, RELEASE_MARKER, TRAILING_MARKERS


def display_title(media_path: str) -> str:
    stem = Path(media_path).stem
    episode = EPISODE_SXE.search(stem) or EPISODE_X.search(stem)
    year = YEAR.search(stem)
    if episode:
        title = YEAR.sub('', stem[:episode.start()])
        suffix = f" · S{int(episode[1]):02d}E{int(episode[2]):02d}"
        if len(episode.groups()) > 2 and episode[3]:
            suffix += f"–E{int(episode[3]):02d}"
    else:
        title = stem[:year.start()] if year else RELEASE_MARKER.sub('', stem)
        suffix = f" ({year[1]})" if year else ''
    title = TRAILING_MARKERS.sub('', title)
    title = re.sub(r'[._\[\](){}]+', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' -')
    return (title or 'Nieznany tytuł') + suffix


def operation_label(task_type: str) -> str:
    if task_type in {'INSPECT', 'INSPECT_SUBTITLES'}:
        return 'Sprawdzanie napisów'
    if task_type in {'PREPARE_TRANSLATION', 'TRANSLATE_TO_POLISH'}:
        return 'Przygotowanie do tłumaczenia'
    return 'Przygotowanie do synchronizacji'
