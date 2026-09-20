# Проверенные интеграции — 20.09.2026

Read-only исследование. Платные запросы/подключения не запускались.
Public JSON Remote OK/Himalayas/Jobicy получен через web; это не smoke из Django/VPS.

| Источник | Контракт и ограничения | Первичный источник |
|---|---|---|
| Remote OK | GET /api; первый элемент metadata; id/position/company/description/date/epoch/location/tags/salary_min/max/url/apply_url. Feed без доказанной полноты. Атрибуция и follow link, no logo without permission. Salary zero unknown. | https://remoteok.com/api |
| Himalayas search | GET /jobs/api/search?q=product%20manager&sort=recent&page=1; page>=1. Country/worldwide/timezone/seniority опциональны. Envelope limit/totalCount, pages=ceil(totalCount/limit); search limit parameter не документирован. | https://himalayas.app/api |
| Himalayas feed | /jobs/api?limit=20&cursor=...; nextCursor, max20. offset deprecated. Live timezoneRestrictions/categories plurals, timestamps seconds vs OpenAPI milliseconds: normalise/test. Daily refresh; 429 backoff60s. | https://himalayas.app/docs/openapi.json |
| Jobicy | /api/v2/remote-jobs?count=200; geo/industry/tag, jobs/jobCount/lastUpdate. id/url/jobSlug/jobTitle/companyName/jobDescription/jobGeo/pubDate, optional salary. Не чаще часа; industry=категория работы, cap means limited. | https://jobicy.com/jobs-rss-feed |
| Web3.career | /api/v1?token=...&tag=...&remote=true&limit=100. Нет cursor/offset. apply_url immutable, attribution follow. Full-description parameter проверить с docs/access, token redaction. | https://docs.bondex.app/api-reference |
| CryptoJobsList | https://api.cryptojobslist.com/public/jobs x-api-key; query/tags/location/remote/publishedAt/page/limit<=100. jobs/meta.totalPages; canonicalURL. Individual access terms, full-description field не проверен с ключом. | https://cryptojobslist.com/api-access |
| RVC | https://app.rvc.global/mcp Streamable HTTP; rvc_search_jobs/get_job; query,conversation_language_code,target_country_codes; worldwide FULLY_REMOTE + []. max10 teaser only, attributed_url. | https://app.rvc.global/for-ai |
| Rocketship | POST /api/openclaw/jobs Bearer; filters.page/itemsPerPage<=50/jobTitleFilters/sortBy DateAdded, includeJobDescription=true; jobOpenings/pagination. 3000 jobs или500 requests/day UTC, active paid plan. TTL24h, no archive. | https://www.remoterocketship.com/api-docs/ ; https://www.remoterocketship.com/terms/ |
| Remocate | Регулярный разрешённый канал ещё не подтверждён, не создавать scraper. | https://www.remocate.app/terms-of-use |
| ITCharm | Штатный export/уведомления/API требуют проверки; пустой web view не доказывает отсутствие вакансий. | https://itcharm.com/vacancies |
| Telegram | User account API для истории, notification bot отдельно; content AI permission не следует из login. | https://core.telegram.org/method/messages.getHistory ; https://core.telegram.org/api/terms ; https://telegram.org/tos/content-licensing |

## Отдельный веб-поиск — кандидат до решения владельца

Tavily search: POST https://api.tavily.com/search, Bearer. query, search_depth basic,
auto_parameters false, max_results5, include_answer false, include_raw_content false,
include_usage true. Snippets не доказательства. Для news time_range year не подменяет event date.
Extract: POST /extract, urls<=8, extract_depth basic, format markdown, timeout10,
include_usage true. results.raw_content и failed_results; 200 с пустым results не успех.
Не задавать extract query для полного текста. Локальный fetcher остаётся защищённым от SSRF;
даже provider-managed fetch получает только валидированные public URLs, без site credentials.

Basic search 1 credit/request, basic extract 1 credit/5 successful URLs. Pricing и
free allowance проверяются перед активацией; никаких обещаний фиксированной бесплатности.
Документация: https://docs.tavily.com/documentation/api-reference/endpoint/search ;
https://docs.tavily.com/documentation/api-reference/endpoint/extract ;
https://docs.tavily.com/documentation/api-credits

## Стек

Django5.2 LTS совместим с Python3.13; patch pin перепроверяется при T01.
https://docs.djangoproject.com/en/5.2/faq/install/ ; https://www.djangoproject.com/download/
OpenAI API/model/structured response контракт проверить в T03 по официальной документации.
Никакой API key или конкретный VPS сейчас не предполагается существующим.
