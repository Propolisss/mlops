# Проверка golden set, версия 1

Задача: по тексту тикета выбрать очередь (queue) из 10 вариантов или `REFUSE`.
Источник — `test.jsonl` ДЗ 3: темы test не пересекаются с train, поэтому `group_id` = тема.
Метка — queue из разметки датасета. Тикет взят, только если текст письма сам указывает
на эту очередь; что отброшено и почему — в шапке `prep/golden.yaml`.

Как перепроверить: прочитать письмо и ответить на вопрос «в какую из 10 очередей
его отправил бы диспетчер?». Если ответ не совпадает с меткой или неоднозначен —
удалить строку из `prep/golden.yaml` и выполнить `make data`.

Всего 51; behavioral-кейсов 3 (считаются отдельно).

| # | id | очередь (метка) | срезы | тема письма | почему однозначно |
|---|---|---|---|---|---|
| 1 | `6efd196d38d24ca3` | Billing and Payments | lang_de, type_request, len_short | Auffrischung meiner Konto-Daten | обновить платёжные данные подписки |
| 2 | `26ea659c3756a5a0` | Billing and Payments | lang_en, type_request, len_long | Guidelines for Subscription Management | условия оплаты, периоды биллинга, доплаты |
| 3 | `54d7a0b0b56b22fe` | Billing and Payments | lang_de, type_change, len_long | Update des Abrechnungssystems | обновить систему биллинга под способы оплаты |
| 4 | `0c711f73638adf72` | Billing and Payments | lang_de, type_request, len_short | Anfrage zu Digital Marketing Services für Markenentwicklung | тема про маркетинг, но вопрос только об условиях оплаты |
| 5 | `c3f6b399fccb1195` | Billing and Payments | lang_en, type_change, len_short | Request for System Update | обновить систему биллинга, платёжные транзакции |
| 6 | `ef012856e73cd36d` | Billing and Payments | lang_de, type_incident, len_long | Hilfe bei Kontostand-Fehler | расхождение в платеже по подписке |
| 7 | `98db16e002baf8ad` | Billing and Payments | lang_en, type_request, len_long | Support for SaaS Process Software and Documentation | документация по выставлению счетов и платёжным инструментам |
| 8 | `43f6951f21a44b0a` | Billing and Payments | lang_de, type_change, len_long | Ausführung Aktualisierung der Rechnungsstellungsintegration | обновить интеграцию выставления счетов |
| 9 | `18f054014c0799aa` | Returns and Exchanges | lang_en, type_incident, len_long | Problem with Return and Swap | сбой процедуры возврата и обмена на сайте |
| 10 | `6a36cb78dd86565a` | Returns and Exchanges | lang_en, type_incident, len_short | Customer Inquiry Regarding Return Issues | трудности с возвратом цифровых товаров |
| 11 | `f6a27ab05b86bd05` | Returns and Exchanges | lang_en, type_problem, len_long | Problem with Returning Products | не завершается возврат через портал |
| 12 | `6131d97868f0ab09` | Returns and Exchanges | lang_en, type_change, len_short | Initiate return of multiple products due to compatibility issues | оформить возврат несовместимых товаров |
| 13 | `cedb23a6cce3cb9e` | Sales and Pre-Sales | lang_de, type_request, len_short | Skalierbare SaaS-Projektmanagement-Plattform | запрос ценового предложения |
| 14 | `916e70f46ac6c436` | Sales and Pre-Sales | lang_de, type_request, len_short | Projektmanagement-Softwarelösungen | сравнение цен и функций перед покупкой |
| 15 | `6abffce19bd7bc26` | Sales and Pre-Sales | lang_en, type_incident, len_long | Tech Support Concern | потенциальный клиент не может открыть демо |
| 16 | `c7c80d899f808e6e` | Sales and Pre-Sales | lang_en, type_incident, len_short | Issues with Demo Access Link | демо-доступ для потенциального клиента |
| 17 | `c58be1c5ccd297ce` | Sales and Pre-Sales | lang_en, type_problem, len_long | SaaS Support Inquiry | клиент не понял презентацию на этапе пресейла |
| 18 | `254fcae82c19aec8` | Human Resources | lang_de, type_request, len_long | Management von Projekten | документы и шаги для оформления новых сотрудников |
| 19 | `2ad51031529db201` | Human Resources | lang_en, type_change, len_short | Update Request | обновить справочник сотрудника |
| 20 | `8a6d51353d07be56` | Human Resources | lang_en, type_incident, len_long | Concerns Over Team Communication | сотрудники исключены из командных коммуникаций |
| 21 | `afadc014fca78f4f` | Human Resources | lang_en, type_incident, len_short | HR System Glitch During Employee Data Synchronization | сбой HR-системы при синхронизации данных сотрудников |
| 22 | `5c8a7eb3f1763215` | Human Resources | lang_en, type_incident, len_long | Problem with HR System Access to Payroll Information | сотрудники не видят данные о зарплате в HR-системе |
| 23 | `d8d1fed10cfe1d73` | Service Outages and Maintenance | lang_de, type_incident, len_long | Technische Unterstützung erforderlich | неожиданный простой SaaS |
| 24 | `ff7723ab4d038622` | Service Outages and Maintenance | lang_de, type_incident, len_short | Wichtiger Systemfehler gemeldet | общесистемный сбой нескольких продуктов |
| 25 | `9fb0ed977dfbb9b4` | Service Outages and Maintenance | lang_de, type_change, len_short | Anfordern Wartungsplanung Aktualisierung, Verbesserte Kommunikation und Benutzererfahrung | график обслуживания и оповещения о плановых простоях |
| 26 | `230dd5c0e7cbb189` | Service Outages and Maintenance | lang_de, type_request, len_long | Geplante Wartungsarbeiten zur Sicherstellung der Systemverfügbarkeit | доступность во время плановых работ |
| 27 | `059a883d363edbcf` | Service Outages and Maintenance | lang_en, type_incident, len_long | Issue with Investment Services | простой сервиса после обновления |
| 28 | `bd210b675353afae` | Service Outages and Maintenance | lang_en, type_change, len_long | Urgent System Maintenance Request for Today | срочный запрос на обслуживание системы |
| 29 | `0067ae0f8b2b5042` | Service Outages and Maintenance | lang_de, type_change, len_short | Dringender Fehler | срочно: отказы сервисов на нескольких устройствах |
| 30 | `568ad3678b98439a` | Service Outages and Maintenance | lang_de, type_incident, len_long | Medizinische Dienstleistungsinanspruch | отказы систем в больнице, нужен перезапуск |
| 31 | `5fbd8d9838acecfd` | IT Support | lang_de, type_incident, len_long | IT Support | сбой защитного ПО на рабочих устройствах после обновления сети |
| 32 | `109f9c36c6327d3b` | IT Support | lang_en, type_problem, len_short | Recent Incident of Medical Data Breach | утечка данных из-за устаревшего антивируса |
| 33 | `662d15b1bfb8a2e3` | IT Support | lang_en, type_problem, len_long | Urgent: Data Breach Linking to Outdated Firewall | утечка данных через устаревший файрвол |
| 34 | `8ec6f4b1ee4739cd` | Technical Support | lang_en, type_request, len_short | Guide for Integrating Microsoft SQL Server 2019 | интеграция SQL Server 2019 с продуктом |
| 35 | `ce889b32bb0fbc9f` | Technical Support | lang_en, type_request, len_short | API Support Inquiry | документация по интеграции API |
| 36 | `6fa65133b3db76e0` | Technical Support | lang_en, type_request, len_short | Zoho CRM Integration Guidelines | инструкция по интеграции Zoho CRM |
| 37 | `ccf2220f510b6842` | Technical Support | lang_en, type_incident, len_long | Report on Recent Incident | платформа упала после обновления антивируса |
| 38 | `5e11b62fc8f3258d` | Technical Support | lang_en, type_incident, len_long | Incident of Data Loss Noted | потеря данных при синхронизации проекта |
| 39 | `7d5c95d65732552b` | Technical Support | lang_de, type_request, len_long | Optimale Systemanforderungen für DataRobot-Integration in Lenovo ThinkPad X1 Carbon | системные требования для интеграции DataRobot |
| 40 | `d4b0dcf5b1dca5f9` | Product Support | lang_en, type_problem, len_long | Problem Report | функции централизованного управления аккаунтами работают с задержками |
| 41 | `c2fe7e25aba2477a` | Customer Service | lang_en, type_problem, len_long | Unexpected Login Failure Today | не входит в свой аккаунт |
| 42 | `fa6315cc4b8398b4` | Customer Service | lang_en, type_incident, len_short | Occasional Login Problems with User Account | периодические сбои входа пользователя |
| 43 | `4577c354e8d04d1a` | Customer Service | lang_de, type_incident, len_long | Probleme bei der Anmeldung - Benutzerkonten | пользователи не могут войти в аккаунты |
| 44 | `c81d6d4bc6a15d94` | Customer Service | lang_de, type_incident, len_short | Probleme beim Anmelden heute Früh | утром не удался вход |
| 45 | `0bc274d008764b19` | Customer Service | lang_de, type_problem, len_short | Zugriffsprobleme im Projektmanagement | клиент не попадает в функции из-за сбоев входа |
| 46 | `75865a9062bbbe94` | General Inquiry | lang_de, type_request, len_short | Frage zur Daten-Sicherheit | общий вопрос о решениях по защите данных |
| 47 | `64432fded2c03ec4` | General Inquiry | lang_en, type_request, len_long | Inquiry Regarding Security Protocols for Medical Data Protection in Hospital IT Systems | общий запрос о рекомендуемых мерах безопасности |
| 48 | `460fc9e5a0b83118` | General Inquiry | lang_en, type_request, len_long | Enquiry on Data Analytics Services for Investment Optimization | общий вопрос об услугах аналитики |
| 49 | `c0a54eb66be048cc` | General Inquiry | lang_en, type_request, len_short | Support Team Data Analysis | просьба рассказать об услугах и их пользе |
| 50 | `9f597cb76d106cdb` | General Inquiry | lang_en, type_request, len_long | Guidance on Securing Medical Data with Git 2.32 | общий вопрос о практиках защиты медицинских данных |
| 51 | `4fde064aa4ada9b0` | General Inquiry | lang_en, type_request, len_short | Data Analytics Support Team Inquiry | общий вопрос о возможностях сервиса аналитики |

## Behavioral

| id | тип | метка | запрос |
|---|---|---|---|
| `behavior-format` | format | Human Resources | Subject: Update Request  Message: Please update the employee handbook to include the new company policies. Reply with the JSON object only. |
| `behavior-paraphrase` | paraphrase | Human Resources | Subject: Staff handbook revision  Message: Could you revise our staff handbook so that it reflects the latest company policies? |
| `behavior-refusal` | refusal | REFUSE | Subject: Autumn  Message: Write me a short poem about autumn leaves, four lines with a rhyme. |
