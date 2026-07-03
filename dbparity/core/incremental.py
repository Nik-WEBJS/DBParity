"""Инкрементальный режим (v0.5): стейт watermark'ов между прогонами.

Сценарий dual-write переключений: после полной сверки нет смысла гонять
миллионы строк заново — достаточно перепроверять строки, изменившиеся
с прошлого прогона. Для каждой таблицы в конфиге задаётся
watermark-колонка (`incremental: {orders: updated_at}`) — она существует
в ОБЕИХ БД и монотонно растёт при изменении строки (timestamp или
числовая версия). После успешной сверки таблицы движок фиксирует максимум
этой колонки; следующий прогон фильтрует обе стороны условием
`wm_col >= watermark` (граница включительно: строки, разделяющие максимум,
перепроверяются — это осознанная страховка от записей «в ту же секунду»).

Стейт — JSON-файл рядом с рабочим каталогом (авто-имя
`.dbparity_incr_<fp12>.json`), валиден только для того же конфига:
отпечаток строится из core.checkpoint.config_fingerprint плюс сама карта
incremental (смена watermark-колонки обесценивает старые значения).
Запись атомарная (tmp + os.replace) и потокобезопасная (лок) — движок
обновляет стейт из рабочих потоков при workers>1.

Сериализация watermark — как у чекпоинтов (checkpoint._wm_encode/_wm_decode):
поддерживаются int, str и интегральные Decimal; прочие типы (float,
datetime-объекты) не сохраняются — обновление молча пропускается, старый
watermark остаётся в силе (безопасное направление: перепроверим больше).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from .checkpoint import _wm_decode, _wm_encode, config_fingerprint

STATE_VERSION = 1       # версия формата ИНКРЕМЕНТАЛЬНОГО стейта (не чекпоинта)
HISTORY_LIMIT = 500     # хранимых записей истории прогонов (старые вытесняются)


def state_fingerprint(config) -> str:
    """Отпечаток конфига для инкрементального стейта.

    База — config_fingerprint (эндпоинты, правила, стратегия, таблицы…),
    плюс карта config.incremental: старый watermark по другой колонке
    неприменим, поэтому её смена тоже инвалидирует стейт.
    """
    base = config_fingerprint(config)
    extra = json.dumps(getattr(config, "incremental", {}) or {},
                       sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{base}|{extra}".encode("utf-8")).hexdigest()


def default_state_path(fingerprint: str) -> str:
    """Авто-имя файла стейта (аналогично авто-имени чекпоинта)."""
    return f".dbparity_incr_{fingerprint[:12]}.json"


class IncrementalState:
    """Watermark'и последней успешной сверки по таблицам (JSON-файл).

    Конструктор создаёт пустой стейт; чтение с диска — через
    classmethod load_or_create (битый или чужой по отпечатку файл
    молча игнорируется — начинаем с чистого стейта).

    Помимо watermark'ов файл хранит "history" — хронологический журнал
    итогов прогонов (record_run): по нему строится отчёт-таймлайн дрейфа
    (`dbparity history`). Ключ появился позже формата v1 и опционален:
    старые файлы без него загружаются как стейт с пустой историей.
    """

    def __init__(self, path, fingerprint: str):
        self.path = Path(path)
        self.fp = fingerprint
        self._lock = threading.Lock()
        self._tables: dict = {}     # таблица → закодированный watermark
        self._history: list = []    # журнал прогонов (записи record_run)

    @classmethod
    def load_or_create(cls, path, fingerprint: str) -> "IncrementalState":
        """Загружает стейт с диска, если файл существует и отпечаток совпал."""
        st = cls(path, fingerprint)
        if st.path.exists():
            try:
                data = json.loads(st.path.read_text(encoding="utf-8"))
                if (data.get("version") == STATE_VERSION
                        and data.get("fingerprint") == fingerprint
                        and isinstance(data.get("tables"), dict)):
                    st._tables = dict(data["tables"])
                    # старые файлы (до появления истории) — без "history";
                    # это не ошибка формата, просто пустой журнал
                    hist = data.get("history")
                    if isinstance(hist, list):
                        st._history = list(hist)
            except (OSError, json.JSONDecodeError):
                pass    # битый/недоступный файл — начинаем заново
        return st

    # ---- чтение ---------------------------------------------------------

    def last_watermark(self, table: str):
        """Watermark таблицы с прошлого прогона либо None (полная сверка)."""
        enc = self._tables.get(table)
        if not isinstance(enc, dict):
            return None
        try:
            return _wm_decode(enc)
        except (KeyError, TypeError, ValueError):
            return None     # рукой правленный/битый элемент — как отсутствие

    @property
    def history(self) -> list:
        """История прогонов (копия; хронологический порядок, свежие в конце).

        Элемент — summary из record_run: {"ts", "full", "equivalent",
        "tables": {таблица: счётчики дрейфа}}.
        """
        return list(self._history)

    # ---- запись ---------------------------------------------------------

    def update(self, table: str, wm) -> None:
        """Фиксирует новый watermark таблицы и сразу сохраняет файл.

        Неэнкодируемый watermark (float, datetime и т.п.) пропускается —
        старое значение остаётся, следующий прогон перепроверит больше строк.
        """
        enc = _wm_encode(wm)
        if enc is None:
            return
        with self._lock:
            self._tables[table] = enc
            self._save_locked()

    def record_run(self, summary: dict) -> None:
        """Дописывает итог прогона в историю и сразу сохраняет файл.

        summary формирует движок в конце run():
        {"ts": iso-UTC, "full": bool, "equivalent": bool,
         "tables": {таблица: {"total_diffs", "mismatched",
                              "missing_in_target", "extra_in_target",
                              "src_rows"}}}.
        Журнал ограничен последними HISTORY_LIMIT записями — стейт-файл
        не разрастается при сверке по расписанию (cron во время dual-write).
        """
        with self._lock:
            self._history.append(summary)
            if len(self._history) > HISTORY_LIMIT:
                self._history = self._history[-HISTORY_LIMIT:]
            self._save_locked()

    def save(self) -> None:
        """Принудительная запись стейта (потокобезопасно)."""
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        """Атомарная запись: tmp-файл + os.replace. Вызывать под локом."""
        payload = {"version": STATE_VERSION, "fingerprint": self.fp,
                   "tables": self._tables, "history": self._history}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.path)      # атомарная подмена
