# RSTELBOT — MVP płatnych treści na Telegramie

RSTELBOT publikuje darmowe posty i zapowiedzi na kanale, a płatne treści przechowuje w prywatnym kanale-magazynie. Użytkownik otrzymuje płatną treść w prywatnej rozmowie po wpisaniu przypisanego do jego Telegram ID kodu.

## Uruchomienie

1. Utwórz bota w BotFather i skopiuj jego token.
2. Dodaj bota jako administratora kanału publicznego i prywatnego kanału-magazynu. Nadaj mu prawo publikowania wiadomości.
3. Skopiuj `.env.example` do `.env` i uzupełnij token, identyfikatory kanałów, ID administratorów oraz hasła MySQL. Hasło w `DATABASE_URL` musi odpowiadać `MYSQL_PASSWORD`.
4. Uruchom z katalogu projektu:

   ```sh
   docker compose up --build -d
   ```

5. Sprawdź logi: `docker compose logs -f bot`. MySQL zapisuje dane w wolumenie `mysql_data`.

## Komendy administratora

Komendy administracyjne są dostępne wyłącznie w prywatnym czacie RSTELBOT dla ID z `ADMIN_IDS`.

- Wyślij wiadomość z treścią do bota i odpowiedz na nią `/free`, aby opublikować ją na kanale.
- Odpowiedz na wiadomość z treścią komendą `/paid <slug> <tytuł>`, aby zapisać płatną treść.
- Użyj `/teaser <slug> <opis>`, aby opublikować lub zaktualizować zapowiedź z przyciskiem „Odblokuj”.
- Użyj `/code <slug> <telegram_id> [dni] [użycia]`, aby wygenerować przypisany kod. Bez `dni` kod nie wygasa; domyślny limit wynosi jedno użycie.
- Dostępne są również `/revoke <kod>`, `/grant <slug> <telegram_id>`, `/ungrant <slug> <telegram_id>`, `/list` i `/stats`.

Użytkownik może użyć `/start <slug>`, `/id`, `/moje` albo wysłać kod jako zwykłą wiadomość prywatną.

## Dane i ograniczenia MVP

Bot nie pobiera plików na dysk. Kopiuje wiadomości do kanału-magazynu, a później wydaje je przez Telegram `copyMessage` z `protect_content=True`. Powiązanie wiadomości źródłowych z kopiami jest przechowywane tymczasowo w tabeli `upload_staging`, więc obsługa albumu przetrwa restart bota przed komendą `/free` lub `/paid`. Elementy albumu są kopiowane i odtwarzane w kolejności jako osobne wiadomości. Płatność odbywa się ręcznie poza botem; specyfikacja nie definiuje kwoty ani pola ceny. Schemat jest tworzony przy starcie przez `create_all`; przed zmianami schematu w działającej instalacji należy dodać migracje Alembic.
