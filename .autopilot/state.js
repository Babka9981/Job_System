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
  "updatedAt": "2026-09-21T16:06:55+03:00",
  "finishedAt": null,
  "stages": [
    {"id":"preflight","status":"done","startedAt":"2026-09-20T15:27:16+03:00","finishedAt":"2026-09-20T15:46:44+03:00","note":"Репозиторий и исходные документы проверены"},
    {"id":"manifest","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"76 требований с трассировкой"},
    {"id":"briefing","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"OpenAI, настраиваемый поиск, CV extraction; search provider уточняется перед T08"},
    {"id":"spec","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"Независимая проверка пройдена"},
    {"id":"plan","status":"done","finishedAt":"2026-09-20T15:46:44+03:00","note":"12 тикетов; реализация ожидает команды"},
    {"id":"build","status":"done","startedAt":"2026-09-20T16:05:00+03:00","finishedAt":"2026-09-21T15:47:36+03:00","note":"T01–T16 завершены"},
    {"id":"review","status":"done","startedAt":"2026-09-20T16:40:00+03:00","finishedAt":"2026-09-21T15:47:36+03:00","note":"T16 принят: manifest/spec done, blocking craft findings нет"},
    {"id":"final","status":"active","startedAt":"2026-09-21T05:29:05+03:00","note":"T16 опубликован в production; ожидается подтверждение профиля и включение monitoring"}
  ],
  "requirements":{"total":89,"done":89,"inTicket":0,"inSpec":0,"placeholder":0,"deferred":0,"dropped":0},
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
    "commit": "4bbc3dc",
    "tests": "Django 19/19; Node drawer 4/4; deploy check 0; migration drift none",
    "retries": 2,
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
    "status": "done",
    "startedAt": "2026-09-20T17:36:00+03:00",
    "finishedAt": "2026-09-20T22:10:00+03:00",
    "commit": "ada9eca",
    "tests": "Django 92/92; Node 6/6; migration drift none",
    "retries": 0,
    "repairs": 2,
    "handoffs": 1,
    "revision": 1,
    "amendment": "G06/D01: cross-source URL alone never merge evidence",
    "priorAttempt": {"retries":2,"repairs":2,"result":"failed before user-confirmed contract"},
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
    "status": "done",
    "startedAt": "2026-09-20T17:36:00+03:00",
    "finishedAt": "2026-09-20T20:40:00+03:00",
    "commit": "b54f2c7",
    "tests": "T03 28/28; full 85/85; Node 6/6; migration drift none",
    "retries": 1,
    "repairs": 2,
    "handoffs": 1,
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
    "status": "done",
    "startedAt": "2026-09-20T22:18:00+03:00",
    "finishedAt": "2026-09-21T00:50:00+03:00",
    "commit": "7ef39a0",
    "tests": "T04 23/23; full Django 137/137; Node 6/6; migration drift none",
    "retries": 0,
    "repairs": 2,
    "handoffs": 1,
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
    "status": "done",
    "startedAt": "2026-09-21T01:42:21+03:00",
    "finishedAt": "2026-09-21T02:08:41+03:00",
    "commit": "ec8dec1",
    "tests": "Django 288/288 (1 skipped); keyed+public 74; Node 6/6; migration drift none; pip check clean",
    "retries": 2,
    "repairs": 3,
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
    "status": "done",
    "startedAt": "2026-09-21T01:40:00+03:00",
    "finishedAt": "2026-09-21T02:08:41+03:00",
    "commit": "c72d8e8",
    "tests": "Django 288/288 (1 skipped); T06 accepted 42/42; Node 6/6; migration drift none",
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
    "status": "done",
    "startedAt": "2026-09-20T22:18:00+03:00",
    "finishedAt": "2026-09-21T01:30:00+03:00",
    "commit": "269b1f8",
    "tests": "T07 29/29; full Django 146/146; Node 6/6; migration drift none",
    "retries": 1,
    "repairs": 1,
    "handoffs": 3,
    "priorAttempt": {"repairs":2,"result":"failed review; fresh-context retry"},
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
    "status": "done",
    "startedAt": "2026-09-21T01:40:00+03:00",
    "finishedAt": "2026-09-21T02:03:44+03:00",
    "commit": "478b8b2",
    "tests": "Django 288/288 (1 skipped); Node 6/6; migration drift none; pip check clean",
    "retries": 2,
    "repairs": 3,
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
    "status": "done",
    "startedAt": "2026-09-21T02:10:50+03:00",
    "finishedAt": "2026-09-21T03:13:21+03:00",
    "commit": "2e0dd65",
    "tests": "Django 336/336 (1 skipped); T09 20; Node 8/8; migration drift none; pip check clean",
    "retries": 0,
    "repairs": 3,
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
    "status": "done",
    "startedAt": "2026-09-21T02:10:50+03:00",
    "finishedAt": "2026-09-21T03:13:21+03:00",
    "commit": "5e5cca4",
    "tests": "Django 336/336 (1 skipped); T10 21; Node 8/8; migration drift none; pip check clean",
    "retries": 0,
    "repairs": 3,
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
    "status": "done",
    "startedAt": "2026-09-21T03:16:01+03:00",
    "finishedAt": "2026-09-21T04:06:19+03:00",
    "commit": "670607f",
    "tests": "Django 345/345 (1 skipped); operations 9/9; Node 8/8; migration drift none; pip check clean; deploy check clean; compose/bash/wizard clean; local image health/restart smoke passed",
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
      "11",
      "13"
    ],
    "wave": 7,
    "zone": [
      "tests/acceptance/",
      "docs/acceptance/"
    ],
    "status": "done",
    "startedAt": "2026-09-21T04:10:00+03:00",
    "finishedAt": "2026-09-21T05:29:05+03:00",
    "commit": "af87dd9",
    "tests": "Django 355/355 (1 skipped); acceptance 4; Node 10/10; parallel browser 50+50 scans; axe serious/critical 0; restore faults pass; migration drift none; pip check clean",
    "retries": 0,
    "repairs": 2,
    "handoffs": 0,
    "githubIssue": "https://github.com/Babka9981/Job_System/issues/12"
  },
  {
    "id": "13",
    "title": "Настройки AI и резервный Brave Search",
    "requirements": ["G08","G09","G01","R45","R50","R51","R52","R64"],
    "blockedBy": ["03","08"],
    "wave": 5,
    "zone": ["jobs/intelligence/search/","jobs/intelligence/research/","jobs/profile/","jobs/templates/profile/","jobs/tests/test_research.py","jobs/tests/test_profile.py",".env.example"],
    "status": "done",
    "startedAt": "2026-09-21T02:03:44+03:00",
    "finishedAt": "2026-09-21T03:13:21+03:00",
    "commit": "3dae049",
    "tests": "Django 336/336 (1 skipped); T13 109; Node 8/8; migration drift none; pip check clean",
    "retries": 0,
    "repairs": 1,
    "handoffs": 0,
    "githubIssue": null
  },
  {
    "id": "14",
    "title": "Brave Search fallback для доступного плана",
    "requirements": ["G09","D02"],
    "blockedBy": ["13"],
    "wave": 8,
    "zone": ["jobs/intelligence/search/brave.py","jobs/tests/test_research.py","docs/planning/integrations.md"],
    "status": "done",
    "startedAt": "2026-09-21T12:05:42+03:00",
    "finishedAt": "2026-09-21T13:34:47+03:00",
    "commit": "54dcee4",
    "tests": "Django 364/364 (1 skipped); research 56/56; Node 10/10; migration drift none; pip check clean; diff check clean",
    "retries": 0,
    "repairs": 1,
    "handoffs": 0,
    "githubIssue": null
  },
  {
    "id": "15",
    "title": "Аккуратная таблица источников",
    "requirements": ["G15"],
    "blockedBy": ["04","12"],
    "wave": 9,
    "zone": ["jobs/templates/sources/","jobs/static/ui/","jobs/tests/test_public_sources.py","tests/acceptance/"],
    "status": "done",
    "startedAt": "2026-09-21T14:15:00+03:00",
    "finishedAt": "2026-09-21T14:40:10+03:00",
    "commit": "3b93181",
    "tests": "Django 364/364 (1 skipped); targeted 2/2; Node 10/10; browser 50 scans, Axe/console clean; migration drift none; diff check clean",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": null
  },
  {
    "id": "16",
    "title": "Читаемая проверка извлечённого CV",
    "requirements": ["G16"],
    "blockedBy": ["03","12"],
    "wave": 10,
    "zone": ["jobs/profile/","jobs/templates/profile/","jobs/static/ui/","jobs/tests/test_profile.py","tests/acceptance/"],
    "status": "done",
    "startedAt": "2026-09-21T15:05:00+03:00",
    "finishedAt": "2026-09-21T15:47:36+03:00",
    "commit": "5283df2",
    "tests": "Django 365/365 (1 skipped); profile 22/22; Node 10/10; browser 50 scans, Axe/console clean; migration drift none; pip/diff check clean",
    "retries": 0,
    "repairs": 0,
    "handoffs": 0,
    "githubIssue": null
  }
],"singlePass":null,"tests":null,
  "debt":{"placeholders":[],"assumptions":[],"emptyEnv":["TELEGRAM_API_ID","TELEGRAM_API_HASH"]},
  "additions":[],"coverage":{"found":9,"fixed":9,"deferred":0,"note":"7 замечаний спецификации и 2 контракта/плана исправлены"},
  "reviewers":{"manifestSpec":"/root/spec_check","craft":"/root/craft_review"},
  "blind":{"status":"partial","checkedAt":"2026-09-21T15:57:34+03:00","productionUpdatedAt":"2026-09-21T16:06:55+03:00","agreed":["local single-owner app","profile/CV","50-source registry","matching","research/drafts","monitoring contracts","backup bundle","design audit","VPS deploy","HTTPS","controlled restart","production owner","production 9+41 source registry","OpenAI live authentication","Tavily live search","Brave application Context-to-Web fallback","Telegram Bot API identity and owner chat","three encrypted production backups","backup timer","unaffected Eggent/SkyPay/blog","Sources table layout: local overflow, sticky context, 10 aligned columns","Sources table production static rollout","Readable CV preview on desktop and mobile","Exact raw CV secondary view and unchanged stored text","CV preview keyboard access and zero page overflow","Readable CV preview production rollout"],"drift":["Telegram MTProto reader awaits API_ID/API_HASH","off-server decrypt/verify and restore drill pending","monitoring remains disabled until CV/profile confirmed_version is greater than zero"],"commands":"Django 365 OK (1 skipped); Node 10 OK; browser 50/50 scans; T16 blind seeded desktop 1440/mobile 390 PASS with exact raw match, keyboard access and zero overflow; production image 20260921T130111Z healthy; HTTPS health/login/static 200; preview CSS present; Eggent 307, SkyPay 200, blog 200; pre-update encrypted backup job-system-20260921T125955Z.tar.age; production aggregates profiles=1, resumes=1, confirmed_profiles=0; backup timer active"},
  "concerns":["DROP T01 browser smoke concern: superseded by T12 real Chrome/Axe 50-scan audit","REPORT Login throttle is process-local; production currently runs one web container","DROP Python mismatch concern: production image now runs pinned Python 3.13.15","REPORT T14 research orchestration currently knows Brave search_context/search_web; consolidate behind an explicit attempt API in a later refactor","REPORT MTProto and off-server restore drill remain operational follow-ups","REPORT T15 test maintainability: Django assertions bind to CSS hooks; browser checks reset 200% before sticky/local-scroll assertions and do not fixture long wrapping; CSS total table width duplicates column-width sum","REPORT T16 preview edge cases: display parser normalizes exotic line separators/final blank lines, numeric marker badges are unbounded, parser boundary assertions and repeated landmark semantics can be tightened; exact raw remains unchanged"]
}
