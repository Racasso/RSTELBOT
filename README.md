# 🤖 RSTELBOT

**RSTELBOT** to zaawansowany i w pełni automatyczny bot Telegrama zaprojektowany do dystrybucji darmowych oraz płatnych treści cyfrowych (takich jak e-booki, pliki PDF, materiały wideo czy grafiki). Projekt bazuje na środowisku **Docker**, relacyjnej bazie danych **MySQL 8.0** oraz frameworku **Python `python-telegram-bot`**.

---

## 🎥 Prezentacja Wideo (YouTube Shorts)

Obejrzyj krótki film pokazujący działanie bota oraz proces odblokowywania treści kodem w praktyce:  
👉 **[Obejrzyj wideo na YouTube](https://www.youtube.com/watch?v=JdF6iIZhemg)**

---

## 🚀 Główne Funkcje

* **Darmowa dystrybucja (`/free`):** Błyskawiczne publikowanie materiałów bezpośrednio na oficjalnym kanale.
* **Magazynowanie treści (Storage):** Bezpieczne przechowywanie kopii zapasowych wszystkich plików w dedykowanym kanale zaplecza.
* **Zapowiedzi (Teasery):** Tworzenie postów promocyjnych na kanale głównym z interaktywnym przyciskiem „Odblokuj” (`/teaser`).
* **System unikalnych kodów dostępu:** Generowanie kodów przypisanych do konkretnego Telegram ID, z opcjonalnymi limitami użyć i terminem ważności (`/code`).
* **Bezpośrednie nadawanie uprawnień (`/grant`):** Szybkie odblokowywanie materiałów z pominięciem kodów.
* **Panel użytkownika (`/moje`):** Umożliwia klientom ponowne pobranie raz zakupionych lub odblokowanych materiałów w dowolnym momencie.
* **Zaawansowana ochrona (`protect_content=True`):** Automatyczne zabezpieczenie wysyłanych plików przed przekazywaniem dalej i pobieraniem oraz ochrona przed atakami Brute-Force.

---

## 🛠️ Wymagania wstępne

Przed uruchomieniem upewnij się, że posiadasz:

1. Zainstalowany **Docker** oraz **Docker Compose**.
2. Utworzonego bota za pośrednictwem [@BotFather](https://t.me/BotFather) na Telegramie.
3. Dwa kanały na Telegramie, gdzie Twój bot jest dodany jako **Administrator** z uprawnieniem do wysyłania/edytowania wiadomości:
   * **Oficjalny Kanał** (widoczny dla klientów)
   * **Kanał Magazynowy / Storage** (prywatne zaplecze bota)

---

## ⚙️ Instalacja i Uruchomienie (Docker)

1. **Sklonuj repozytorium** lub umieść pliki projektu w wybranym katalogu na swoim komputerze/serwerze.
2. **Skonfiguruj zmienne środowiskowe** w pliku `.env`:
   * `TELEGRAM_BOT_TOKEN` – token dostępowy Twojego bota od BotFather.
   * `ADMIN_IDS` – Twoje Telegram ID (uprawniające do dostępu do panelu administratora).
   * `CHANNEL_ID` – ID oficjalnego kanału (np. `-100...`).
   * `STORAGE_CHAT_ID` – ID kanału magazynowego (np. `-100...`).
   * Dane dostępowe do bazy MySQL.
3. **Uruchom kontenery w tle:**
   ```bash
   docker-compose up --build -d
   ```
4. **Sprawdź logi**, aby upewnić się, że baza MySQL i bot wystartowały bez błędów:
   ```bash
   docker-compose logs -f bot
   ```

---

## 📖 PEŁNA INSTRUKCJA OBSŁUGI

### 👤 Dla Użytkowników / Klientów

* **`/start`** – Rozpoczyna rozmowę z botem i wyświetla Twój unikalny Telegram ID.
* **`/start <slug>`** – Otwiera kartę płatnej treści na podstawie jej unikalnego oznaczenia (np. `/start kurs-python`). Wyświetla szczegóły zamówienia oraz instruuje, jak przekazać ID administratorowi po płatności.
* **`/id`** – Wyświetla Twój aktualny Telegram ID (przydatne do skopiowania i wysłania sprzedawcy).
* **Wpisanie kodu** – Po opłaceniu treści otrzymasz od administratora kod (np. `BOX-8K3M-92PL`). Wpisz go jako **zwykłą wiadomość tekstową** w oknie czatu z botem, aby natychmiast otrzymać plik.
* **`/moje`** – Wyświetla listę wszystkich przypisanych do Ciebie materiałów i przesyła je ponownie na czat.

---

### 👑 Dla Administratorów

> *Wszystkie komendy administracyjne działają wyłącznie w prywatnym czacie z botem i tylko dla numerów ID zdefiniowanych w zmiennej `ADMIN_IDS`.*

#### 1. Publikacja darmowej treści na kanale (`/free`)
1. Wyślij plik, zdjęcie lub dokument bezpośrednio do bota na czacie prywatnym.
2. Zrób **Reply (Odpowiedz)** na tę wiadomość z plikiem i wyślij komendę:
   ```text
   /free
   ```
   *(Bot automatycznie opublikuje materiał na kanale oficjalnym).*

#### 2. Dodawanie płatnej treści do magazynu (`/paid`)
1. Wyślij materiał (np. PDF lub zdjęcie) do bota.
2. Zrób **Reply (Odpowiedz)** na tę wiadomość, podając unikalny skrót (*slug*) oraz czytelny tytuł:
   ```text
   /paid <slug> <tytuł>
   ```
   *Przykład:* `/paid kurs-python Poradnik Python Automation MVP`

#### 3. Publikacja zapowiedzi (Teaser) na kanale (`/teaser`)
Wyślij w czacie z botem komendę publikującą lub aktualizującą post promocyjny na kanale głównym:
```text
/teaser <slug> <opis>
```
*Przykład:* `/teaser kurs-python Odbierz kompletny poradnik i automatyzuj swój biznes! Kliknij Odblokuj poniżej.`

#### 4. Zarządzanie kodami i dostępami
* **Generowanie kodu dostępu dla klienta:**
  ```text
  /code <slug> <telegram_id> [dni] [użycia]
  ```
  *Przykłady:*
  * `/code kurs-python 7617177383` (kod jednorazowy, bezterminowy)
  * `/code kurs-python 7617177383 30 2` (kod ważny 30 dni, maksymalnie 2 użycia)
* **Unieważnienie kodu:**
  ```text
  /revoke <kod>
  ```
* **Bezpośrednie nadanie dostępu (pomijając kody):**
  ```text
  /grant <slug> <telegram_id>
  ```
* **Odebranie ręcznie nadanego dostępu:**
  ```text
  /ungrant <slug> <telegram_id>
  ```

#### 5. Informacje i Statystyki
* **`/list`** – Wyświetla zestawienie wszystkich zapisanych w bazie treści oraz liczbę użytkowników mających do nich dostęp.
* **`/stats`** – Pokazuje ogólne podsumowanie statystyczne bota.

---

## 🔄 Przykładowy Workflow Zakupu

1. Klient widzi zapowiedź (*Teaser*) na kanale oficjalnym i klika przycisk **„Odblokuj”**, który przenosi go do czatu z botem.
2. Klient sprawdza swoje ID komendą `/id` i przesyła je sprzedawcy po opłaceniu zamówienia poza botem.
3. Administrator generuje unikalny kod za pomocą komendy `/code <slug> <id>` i przekazuje go klientowi.
4. Klient wkleja kod na czacie z botem jako zwykłą wiadomość i natychmiast otrzymuje zabezpieczony plik.
5. W przyszłości klient może w każdej chwili odzyskać dostęp do materiałów, wpisując komendę `/moje`.

---

## 📝 Licencja

Projekt przeznaczony do użytku prywatnego, hobbystycznego oraz komercyjnego w celu automatyzacji sprzedaży treści cyfrowych.