window.STATE =
{
  "slug": "job-search-mvp",
  "dir": "2026-09-20-job-search-mvp--wip",
  "title": "Персональный поиск работы — разработка",
  "mode": "semi",
  "depth": "normal",
  "polish": null,
  "tier": "T3",
  "executionGate": null,
  "briefFile": "2026-09-20-brief.md",
  "memoryFile": "AGENTS.md",
  "skillDir": "C:/Users/IliaF/.codex/skills/autopilot",
  "startedAt": "2026-09-20T15:27:16+03:00",
  "updatedAt": "2026-09-20T17:30:00+03:00",
  "finishedAt": null,
  "stages": [
    {"id":"preflight","status":"done","startedAt":"2026-09-20T15:27:16+03:00","finishedAt":"2026-09-20T15:46:44+03:00","note":"Репозиторий и исходные документы проверены"},
    {"id":"manifest","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"76 требований с трассировкой"},
    {"id":"briefing","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"OpenAI, настраиваемый поиск, CV extraction; search provider уточняется перед T08"},
    {"id":"spec","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"Независимая проверка пройдена"},
    {"id":"plan","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"12 тикетов; реализация ожидает команды"},
    {"id":"build","status":"active","startedAt":"2026-09-20T16:05:00+03:00","note":"T01 завершён; готова волна T02/T03"},
    {"id":"review","status":"active","startedAt":"2026-09-20T16:40:00+03:00","note":"T01: manifest/spec и craft review"},
    {"id":"final","status":"pending"}
  ],
  "requirements":{"total":76,"done":1,"inTicket":75,"inSpec":0,"placeholder":0,"deferred":0,"dropped":0},
  "tickets":[
  {
    "id": "01",
    "title": "Закрытый вход и общая оболочка",
    "requirements": [
      "R01",
      "R02",
      "R65",
      "R66",
      "G04"
    ],
    "blockedBy": [],
    "wave": 1,
    "zone": [
      "config/",
      "jobs/core/",
      "jobs/models/",
      "jobs/templates/shared/",
      "jobs/static/ui/"
    ],
    "status": "done",
    "startedAt": "2026-09-20T16:05:00+03:00",
    "finishedAt": "2026-09-20T17:30:00+03:00",
    "tests": "Django 19/19; Node drawer 4/4; deploy check 0; migration drift none",
    "retries": 1,
    "repairs": 2,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/1"
  },
  {
    "id": "02",
    "title": "Вакансии, ручное добавление и статусы",
    "requirements": [
      "R02",
      "R07",
      "R11",
      "R13",
      "R14",
      "R15",
      "R22",
      "R23",
      "R26",
      "R60",
      "R61",
      "R62"
    ],
    "blockedBy": [
      "01"
    ],
    "wave": 2,
    "zone": [
      "jobs/vacancies/",
      "jobs/templates/vacancies/",
      "jobs/tests/test_vacancies.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/2"
  },
  {
    "id": "03",
    "title": "CV через LLM, проверяемый профиль и настройки",
    "requirements": [
      "R02",
      "R03",
      "R04",
      "R05",
      "R06",
      "R16",
      "R17",
      "R18",
      "R19",
      "R45",
      "R49",
      "R56",
      "R64",
      "R66",
      "G01",
      "G02",
      "G03"
    ],
    "blockedBy": [
      "01"
    ],
    "wave": 2,
    "zone": [
      "jobs/profile/",
      "jobs/intelligence/gateway.py",
      "jobs/intelligence/budget.py",
      "jobs/templates/profile/",
      "jobs/tests/test_profile.py",
      "jobs/tests/test_budget.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/3"
  },
  {
    "id": "04",
    "title": "50 источников и сбор открытых API",
    "requirements": [
      "R02",
      "R20",
      "R21",
      "R28",
      "R29",
      "R35",
      "R36",
      "R38",
      "R57",
      "R60",
      "R63"
    ],
    "blockedBy": [
      "01",
      "02"
    ],
    "wave": 3,
    "zone": [
      "jobs/sources/core/",
      "jobs/sources/public/",
      "jobs/templates/sources/",
      "jobs/tests/test_public_sources.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/4"
  },
  {
    "id": "05",
    "title": "API с ключами, RVC и временные карточки",
    "requirements": [
      "R21",
      "R30",
      "R31",
      "R32",
      "R33",
      "R34",
      "R37"
    ],
    "blockedBy": [
      "04"
    ],
    "wave": 4,
    "zone": [
      "jobs/sources/keyed/",
      "jobs/sources/rvc/",
      "jobs/tests/test_keyed_sources.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/5"
  },
  {
    "id": "06",
    "title": "Telegram-источники и допустимая обработка",
    "requirements": [
      "R20",
      "R38",
      "R39",
      "R40",
      "R41",
      "R42",
      "R66"
    ],
    "blockedBy": [
      "04"
    ],
    "wave": 4,
    "zone": [
      "jobs/sources/telegram/",
      "jobs/templates/sources/telegram/",
      "jobs/tests/test_telegram.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/6"
  },
  {
    "id": "07",
    "title": "Оценка вакансий и сопоставимость условий",
    "requirements": [
      "R03",
      "R04",
      "R05",
      "R06",
      "R07",
      "R08",
      "R09",
      "R10",
      "R11",
      "R12",
      "R13",
      "R24",
      "R25",
      "R27",
      "R28",
      "R42",
      "R64",
      "G01"
    ],
    "blockedBy": [
      "02",
      "03"
    ],
    "wave": 3,
    "zone": [
      "jobs/matching/",
      "jobs/tests/test_matching.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/7"
  },
  {
    "id": "08",
    "title": "Веб-исследование компании и доказательства",
    "requirements": [
      "R45",
      "R46",
      "R47",
      "R48",
      "R49",
      "R50",
      "R51",
      "R52",
      "R53",
      "R54",
      "R55",
      "R67",
      "G01"
    ],
    "blockedBy": [
      "03",
      "07"
    ],
    "wave": 4,
    "zone": [
      "jobs/intelligence/research/",
      "jobs/intelligence/search/",
      "jobs/intelligence/fetch/",
      "jobs/tests/test_research.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/8"
  },
  {
    "id": "09",
    "title": "Два отклика с редактированием и provenance",
    "requirements": [
      "R19",
      "R23",
      "R42",
      "R43",
      "R44",
      "R45",
      "R49",
      "R54",
      "R55",
      "G01"
    ],
    "blockedBy": [
      "02",
      "03",
      "08"
    ],
    "wave": 5,
    "zone": [
      "jobs/drafts/",
      "jobs/templates/vacancies/drafts/",
      "jobs/tests/test_drafts.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/9"
  },
  {
    "id": "10",
    "title": "Расписание, восстановление и Telegram-подборка",
    "requirements": [
      "R56",
      "R57",
      "R58",
      "R59",
      "R63"
    ],
    "blockedBy": [
      "04",
      "05",
      "06",
      "07"
    ],
    "wave": 5,
    "zone": [
      "jobs/monitoring/",
      "jobs/notifications/",
      "jobs/tests/test_monitoring.py"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/10"
  },
  {
    "id": "11",
    "title": "VPS, HTTPS, резервирование и срок хранения",
    "requirements": [
      "R01",
      "R32",
      "R65",
      "R66",
      "R68",
      "R69"
    ],
    "blockedBy": [
      "05",
      "06",
      "09",
      "10"
    ],
    "wave": 6,
    "zone": [
      "deployment/",
      "jobs/operations/",
      "docs/operations/"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/11"
  },
  {
    "id": "12",
    "title": "Сквозная приёмка пилота и дизайн-аудит",
    "requirements": [
      "R38",
      "R70",
      "R71",
      "G04"
    ],
    "blockedBy": [
      "01",
      "02",
      "03",
      "04",
      "05",
      "06",
      "07",
      "08",
      "09",
      "10",
      "11"
    ],
    "wave": 7,
    "zone": [
      "tests/acceptance/",
      "docs/acceptance/"
    ],
    "status": "pending",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/12"
  }
],"singlePass":null,"tests":null,
  "debt":{"placeholders":[],"assumptions":[],"emptyEnv":[]},
  "additions":[],"coverage":{"found":9,"fixed":9,"deferred":0,"note":"7 замечаний спецификации и 2 контракта/плана исправлены"},"concerns":["T01: интерактивный browser smoke не стартовал из-за failed to write kernel assets; UI покрыт HTTP/JS/ARIA и bootstrap Node-тестами","T01: process-local login throttle до deployment должен получить общий атомарный cache backend","T01: проект закрепляет Python 3.13.15, локальный runner проверки был 3.13.7"],
  "reviewers":{"manifestSpec":"/root/spec_check","craft":"/root/craft_review"},"blind":null
}
