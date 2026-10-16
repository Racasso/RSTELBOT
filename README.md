# RSTELBOT

RSTELBOT to asynchroniczny bot Telegrama do prezentowania i dystrybucji treści cyfrowych. Płatne materiały są udostępniane po potwierdzeniu płatności Telegram Stars. Bot przechowuje treści w prywatnym kanale-magazynie Telegrama i kopiuje je odbiorcom — nie pobiera plików na dysk.

## Funkcje

- Sprzedaż e-booków, plików, wideo i grafik za Telegram Stars (`XTR`).
- Podgląd produktu: indywidualny albo domyślny obraz dla produktów bez własnego podglądu.
- Dostarczanie materiału po potwierdzeniu płatności przez Telegram, z `protect_content=True`.
- Ponowne wysyłanie odblokowanych materiałów przez `/moje`.
- Publikowanie bezpłatnych materiałów i zapowiedzi produktów na kanale.
- Trwała kolejka zaplanowanych publikacji przechowywana w MySQL i odtwarzana po restarcie.
- Administracyjne kody dostępu i ręczne nadawanie dostępu jako wyjątki od standardowej ścieżki zakupu Stars.
- Ograniczenie prób wpisania błędnego kodu: maksymalnie 5 prób w ciągu 10 minut na użytkownika.

## Co nowego

- Standardowy zakup płatnych treści przez faktury Telegram Stars.
- Ceny produktów zapisywane w bazie oraz podglądy domyślne i przypisane do produktu.
- Planowanie zapowiedzi i publikacji bezpłatnych materiałów.
- Zachowanie kodów i ręcznego nadawania dostępu dla administracyjnej obsługi wyjątków.

## Technologie

- Python 3.12 w obrazie Dockera (kod wymaga Pythona 3.11 lub nowszego).
- `python-telegram-bot` 21+ z JobQueue, tryb asynchroniczny i long polling.
- MySQL 8, SQLAlchemy 2 async i `aiomysql`.
- Docker Compose.

Bot nie uruchamia serwera WWW ani webhooka. Zaplanowane publikacje obsługuje PTB JobQueue, a ich stan jest zapisany w MySQL.

## Wymagania

- Docker z obsługą Docker Compose.
- Bot utworzony przez [@BotFather](https://t.me/BotFather).
- Publiczny kanał oraz prywatny kanał-magazynowy Telegrama.
- Bot dodany jako administrator obu kanałów. Do publikacji i edycji postów potrzebuje odpowiednich uprawnień.

## Konfiguracja i uruchomienie

1. Utwórz plik `.env` w katalogu głównym na podstawie dołączonej przykładowej konfiguracji `.envEXAMP.example`.
2. Ustaw wymagane zmienne:

   | Zmienna | Znaczenie |
   | --- | --- |
   | `BOT_TOKEN` | Token bota z BotFather. |
   | `ADMIN_IDS` | Telegram ID administratorów, oddzielone przecinkami. |
   | `CHANNEL_ID` | ID publicznego kanału. |
   | `STORAGE_CHAT_ID` | ID prywatnego kanału-magazynu. |
   | `DATABASE_URL` | Adres MySQL w formacie `mysql+aiomysql://...`. Dane muszą odpowiadać konfiguracji MySQL poniżej. |
   | `MYSQL_DATABASE` | Nazwa bazy MySQL. |
   | `MYSQL_USER` | Użytkownik MySQL. |
   | `MYSQL_PASSWORD` | Hasło użytkownika MySQL. |
   | `MYSQL_ROOT_PASSWORD` | Hasło administratora MySQL wymagane przez kontener. |
   | `TERMS_URL` | Publicznie dostępny link do regulaminu. |
   | `SUPPORT_CONTACT` | Kontakt do obsługi płatności, np. login Telegram lub e-mail. |
   | `TERMS_VERSION` | Wersja regulaminu zapisywana przy akceptacji; domyślnie `1`. |
   | `BOT_TIMEZONE` | Strefa czasowa harmonogramu; domyślnie `Europe/Warsaw`. |

   `TERMS_URL` i `SUPPORT_CONTACT` muszą być ustawione, aby bot pokazał przycisk zakupu. Jeśli hasło MySQL zawiera znaki specjalne, zakoduj je poprawnie w `DATABASE_URL`.

3. Uruchom bazę i bota:

   ```bash
   docker compose up -d --build
   ```

4. Sprawdź kontenery i logi:

   ```bash
   docker compose ps
   docker compose logs -f bot
   ```

Zatrzymanie kontenerów bez usuwania bazy:

```bash
docker compose down
```

Wolumen `mysql_data` przechowuje dane MySQL. Nie używaj `docker compose down -v`, jeśli chcesz zachować bazę. Przy starcie RSTELBOT tworzy brakujące tabele i dodaje nowe pola do istniejącego schematu MVP. Po migracji wcześniejsze płatne produkty nie mają ustawionej ceny Stars — ustaw ją komendą `/price`.

**Bezpieczeństwo:** nie publikuj ani nie commituj pliku `.env`. `.dockerignore` wyklucza go z obrazu Dockera, ale nie zastępuje reguły ignorowania pliku przez Git.

## Użycie dla użytkowników

| Komenda | Działanie |
| --- | --- |
| `/start` | Pokazuje Telegram ID i krótką instrukcję. |
| `/start <slug>` | Pokazuje kartę płatnego produktu, podgląd i cenę Stars. |
| `/id` | Pokazuje Telegram ID użytkownika. |
| `/moje` | Ponownie wysyła wszystkie odblokowane materiały. |
| `/terms` | Pokazuje skonfigurowany regulamin. |
| `/paysupport` | Pokazuje kontakt do obsługi płatności. |

Aby kupić produkt, użytkownik otwiera `/start <slug>`, naciska przycisk zakupu i opłaca fakturę Stars. Dostęp jest przyznawany po otrzymaniu przez bota `successful_payment`; samo potwierdzenie pre-checkout nie wystarcza. Link „Odblokuj” w zapowiedzi na kanale otwiera kartę produktu w bocie.

## Komendy administratora

Wszystkie komendy administratora działają wyłącznie w prywatnym czacie i tylko dla Telegram ID z `ADMIN_IDS`.

### Produkty i podglądy

Najpierw wyślij materiał botowi. Następnie odpowiedz na jego wiadomość komendą. Obsługiwane przykłady to zdjęcia, wideo i dokumenty, w tym e-booki PDF.

| Komenda | Działanie |
| --- | --- |
| `/paid <slug> <gwiazdki> <tytuł>` | Zapisuje płatny produkt i cenę. Odpowiedz na wiadomość z materiałem. |
| `/price <slug> <gwiazdki>` | Ustawia lub zmienia cenę istniejącego płatnego produktu. |
| `/preview_default` | Ustawia domyślny obraz podglądowy. Odpowiedz na zdjęcie. |
| `/preview <slug>` | Ustawia podgląd konkretnego produktu. Odpowiedz na zdjęcie. |
| `/preview_clear <slug>` | Usuwa podgląd produktu; bot użyje domyślnego. |
| `/free` | Natychmiast publikuje bezpłatny materiał. Odpowiedz na wiadomość z materiałem. |

Zdjęcie podglądowe należy wysłać w docelowej jakości. RSTELBOT przechowuje kopię w kanale-magazynie i sam nie zmniejsza rozdzielczości.

### Publikacje na kanale

| Komenda | Działanie |
| --- | --- |
| `/teaser <slug> <opis>` | Publikuje lub aktualizuje zapowiedź płatnego produktu. |
| `/schedule <slug> <RRRR-MM-DDTHH:MM> <opis>` | Planuje zapowiedź płatnego produktu. Czas lokalny według `BOT_TIMEZONE`. |
| `/schedule_free <RRRR-MM-DDTHH:MM>` | Planuje publikację bezpłatnego materiału. Odpowiedz na jego wiadomość. |
| `/scheduled` | Pokazuje oczekujące i nieudane publikacje. |
| `/schedule_cancel <numer>` | Anuluje oczekującą publikację. Numer pobierz z `/scheduled`. |

Przykład zaplanowania zapowiedzi:

```text
/schedule ebook-start 2027-05-12T18:30 Nowy e-book jest dostępny
```

Czas musi być przyszły. Harmonogram publikuje zapowiedzi płatnych produktów i wskazane bezpłatne materiały. Płatna treść pozostaje dostępna po zakupie Stars albo nadaniu dostępu przez administratora.

### Kody i ręczne dostępy

To administracyjne wyjątki od standardowego zakupu Stars.

| Komenda | Działanie |
| --- | --- |
| `/code <slug> <telegram_id> [dni] [użycia]` | Generuje kod przypisany do Telegram ID. Domyślnie bez terminu i na jedno użycie. |
| `/revoke <kod>` | Unieważnia kod. |
| `/grant <slug> <telegram_id>` | Nadaje użytkownikowi dostęp. |
| `/ungrant <slug> <telegram_id>` | Odbiera użytkownikowi dostęp. |
| `/list` | Pokazuje produkty, ceny i liczbę dostępów. |
| `/stats` | Pokazuje liczbę użytkowników, produktów, dostępów, kodów i płatności Stars. |

Użytkownik wprowadza kod jako zwykły tekst w prywatnym czacie z botem. Błędne próby są ograniczone do pięciu w ciągu 10 minut na użytkownika.

## Płatności, regulamin i wsparcie

Płatności za treści cyfrowe są realizowane w Telegram Stars (`XTR`). RSTELBOT zapisuje zamówienie, obsługuje pre-checkout i dostarcza materiał dopiero po potwierdzeniu płatności. Zobacz [dokumentację płatności Telegram Stars](https://core.telegram.org/bots/payments-stars). Telegram wymaga dostępnego regulaminu oraz kontaktu do obsługi płatności.

Przed sprzedażą sprawdź, czy proces zakupu spełnia wymagania prawne dotyczące dostarczania treści cyfrowych. Obecny przycisk zakupu łączy akceptację regulaminu i zakup; nie zbiera osobnego potwierdzenia zgody na natychmiastowe dostarczenie treści i informacji o skutkach dla prawa odstąpienia. Więcej informacji: [UOKiK — wyjątki od prawa odstąpienia](https://prawakonsumenta.uokik.gov.pl/prawo-odstapienia-od-umowy/wylaczenia-prawa-do-odstapienia/).

## Struktura projektu

```text
bot/
├── handlers/       # Komendy administratora i użytkownika
├── services/       # Logika treści, dostępów, kodów i płatności
├── config.py       # Konfiguracja środowiskowa
├── db.py           # Async SQLAlchemy i inicjalizacja schematu
├── main.py         # Long polling i rejestracja handlerów
└── models.py       # Modele SQLAlchemy
Dockerfile
docker-compose.yml
requirements.txt
```
