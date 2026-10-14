"""English UI strings, keyed by the Dutch original."""

EN: dict[str, str] = {
    # auth
    "Inloggen": "Log in",
    "Gebruikersnaam": "Username",
    "Wachtwoord": "Password",
    "Verificatiecode": "Verification code",
    "Typ de code uit je authenticator-app, of een van je herstelcodes.": (
        "Enter the code from your authenticator app, or one of your recovery codes."
    ),
    "Code": "Code",
    "Verder": "Continue",
    "Terug": "Back",
    "Kies een wachtwoord": "Choose a password",
    "Je hebt een tijdelijk wachtwoord gekregen. Kies nu je eigen wachtwoord van minstens 10 tekens.": (
        "You were given a temporary password. Choose your own password of at least 10 characters."
    ),
    "Nieuw wachtwoord": "New password",
    "Nog een keer": "Repeat",
    "Wachtwoord opslaan": "Save password",
    "Herstelcodes": "Recovery codes",
    "Bewaar je herstelcodes": "Save your recovery codes",
    "Met een herstelcode kun je inloggen als je je telefoon kwijt bent. Elke code werkt één keer. Ze worden hierna niet meer getoond.": (
        "A recovery code lets you log in if you lose your phone. Each code works once. "
        "They won't be shown again."
    ),
    "Kopiëren": "Copy",
    "Gekopieerd": "Copied",
    "Ik heb ze bewaard": "I've saved them",
    "Tweestapsverificatie": "Two-step verification",
    "Tweestapsverificatie instellen": "Set up two-step verification",
    "Scan de code met een authenticator-app (zoals 1Password, Bitwarden of Google Authenticator) en typ de code van zes cijfers die de app laat zien.": (
        "Scan the code with an authenticator app (such as 1Password, Bitwarden or Google "
        "Authenticator) and enter the six-digit code it shows."
    ),
    "Kun je niet scannen?": "Can't scan?",
    "Voer deze sleutel handmatig in:": "Enter this key manually:",
    "Inschakelen": "Turn on",
    "Toegang geven": "Allow access",
    "Deze link is verlopen": "This link has expired",
    "Start het koppelen opnieuw vanuit de app die toegang vraagt.": (
        "Start connecting again from the app that asks for access."
    ),
    "{name} wil toegang tot Schrift": "{name} wants access to Schrift",
    "Daarmee kan deze app je notities en taken lezen, maken, wijzigen en naar de prullenbak verplaatsen. Elke wijziging wordt bewaard als versie, zodat je die kunt terugdraaien.": (
        "The app can then read, create and change your notes and tasks, and move them to the "
        "trash. Every change is kept as a version, so you can undo it."
    ),
    "Ingelogd als": "Logged in as",
    "Weigeren": "Deny",
    "Terug naar:": "Returns to:",
    "Te veel pogingen. Probeer het over een kwartier opnieuw.": (
        "Too many attempts. Try again in fifteen minutes."
    ),
    "Gebruikersnaam of wachtwoord klopt niet.": "Username or password is incorrect.",
    "Die code klopt niet. Probeer de volgende code uit je app.": (
        "That code is incorrect. Try the next code from your app."
    ),
    "Die code klopt niet.": "That code is incorrect.",
    "De wachtwoorden zijn niet gelijk.": "The passwords don't match.",
    "Het wachtwoord moet minstens 10 tekens hebben.": "The password needs at least 10 characters.",
    # shell
    "Navigatie": "Navigation",
    "Menu sluiten": "Close menu",
    "Zoeken": "Search",
    "Zoeken in notities": "Search notes",
    "Niets gevonden.": "Nothing found.",
    "Taken": "Tasks",
    "Inbox": "Inbox",
    "Mappen": "Folders",
    "Nieuwe map": "New folder",
    "Naam van de map": "Folder name",
    "Nog geen mappen. Maak er een met de plus.": "No folders yet. Create one with the plus.",
    "Tags": "Tags",
    "Prullenbak": "Trash",
    "Licht of donker thema": "Light or dark theme",
    "Instellingen": "Settings",
    "Hoofdmenu": "Main menu",
    "Notities": "Notes",
    "Nieuw": "New",
    "Sluiten": "Close",
    "Ongedaan maken": "Undo",
    "Menu": "Menu",
    # tree
    "Nieuwe notitie": "New note",
    "Nieuwe notitie in {name}": "New note in {name}",
    "Meer acties voor {name}": "More actions for {name}",
    "Nieuwe submap": "New subfolder",
    "Naam van de submap": "Subfolder name",
    "Hernoemen": "Rename",
    "Nieuwe naam": "New name",
    "Verwijderen": "Delete",
    "Notities in deze map gaan naar de prullenbak.": "Notes in this folder go to the trash.",
    "Map verwijderen": "Delete folder",
    "Map verwijderen?": "Delete folder?",
    # editor
    "Opslaan…": "Saving…",
    "Opgeslagen": "Saved",
    "Niet opgeslagen": "Not saved",
    "Naar prullenbak verplaatst": "Moved to trash",
    "Er ging iets mis.": "Something went wrong.",
    "Typ / voor opmaak": "Type / for formatting",
    "Gewijzigd door": "Changed by",
    "Bijgewerkt door Claude": "Updated by Claude",
    "Datum wissen": "Clear date",
    "Tijd": "Time",
    "Vandaag": "Today",
    "Morgen": "Tomorrow",
    "Volgende week": "Next week",
    "Kop 1": "Heading 1",
    "Kop 2": "Heading 2",
    "Kop 3": "Heading 3",
    "Takenlijst": "Task list",
    "Opsomming": "Bullet list",
    "Genummerde lijst": "Numbered list",
    "Citaat": "Quote",
    "Uitklapblok": "Collapsible block",
    "Tabel": "Table",
    "Scheidingslijn": "Divider",
    "Datum": "Date",
    "Link naar notitie": "Link to note",
    "Bestand of afbeelding": "File or image",
    "Uploaden…": "Uploading…",
    "Geen notities gevonden": "No notes found",
    "Samenvatting": "Summary",
    "Details": "Details",
    "Versies": "Versions",
    "Versies bekijken": "View versions",
    "Markdown downloaden": "Download Markdown",
    "Verplaatsen naar": "Move to",
    "Kies een map": "Choose a folder",
    "(geen map)": "(no folder)",
    "Naar prullenbak": "Move to trash",
    "Deze notitie is intussen elders gewijzigd.": "This note was changed elsewhere in the meantime.",
    "Hun versie laden": "Load their version",
    "Mijn versie bewaren": "Keep my version",
    "Losse taken en snelle notities. Taken die je in je herinneringen-app aanmaakt, komen hier.": (
        "Loose tasks and quick notes. Tasks you create in your reminders app land here."
    ),
    "Gelinkt vanuit": "Linked from",
    "Bijlagen": "Attachments",
    "Verwijzingen en bijlagen": "Links and attachments",
    "Deze versie terugzetten": "Restore this version",
    "Klaar": "Done",
    "Opmaak": "Formatting",
    "Kop": "Heading",
    "Vet": "Bold",
    "Meer acties": "More actions",
    "Titel": "Title",
    "Tag verwijderen": "Remove tag",
    "Tag toevoegen": "Add tag",
    "Niet gevonden": "Not found",
    "Deze notitie bestaat niet (meer).": "This note doesn't exist (anymore).",
    "Kijk in de prullenbak": "Look in the trash",
    "Naar start": "Go to start",
    # tasks
    "Alles": "All",
    "Toon afgerond": "Show completed",
    "Niets te doen.": "Nothing to do.",
    "Typ hierboven een taak, of zet '- [ ]' voor een regel in een notitie.": (
        "Type a task above, or start a line in a note with '- [ ]'."
    ),
    "Nieuwe taak, Enter om toe te voegen aan Inbox": "New task, Enter adds it to the Inbox",
    "Nieuwe taak": "New task",
    "Afvinken": "Complete",
    "Verlopen": "Overdue",
    "Komende 7 dagen": "Next 7 days",
    "Later": "Later",
    "Zonder datum": "No date",
    "Afgerond": "Completed",
    "Typ eerst een taak.": "Type a task first.",
    # search & trash
    "vergelijkbaar": "similar",
    "Niets gevonden voor '{q}'.": "Nothing found for '{q}'.",
    "Zoek in titels, tekst, tags en bijlagen": "Search titles, text, tags and attachments",
    "Prullenbak legen": "Empty trash",
    "Alles definitief verwijderen": "Delete everything for good",
    "Terugzetten": "Restore",
    "Definitief": "For good",
    "De prullenbak is leeg.": "The trash is empty.",
    "Definitief verwijderd.": "Deleted for good.",
    "Prullenbak geleegd.": "Trash emptied.",
    # settings
    "Secties": "Sections",
    "Algemeen": "General",
    "Beveiliging": "Security",
    "MCP-server": "MCP server",
    "Gebruikers": "Users",
    "Taal": "Language",
    "Thema": "Theme",
    "Volg systeem": "Follow system",
    "Licht": "Light",
    "Donker": "Dark",
    "Tijdzone": "Time zone",
    "Opslaan": "Save",
    "Opgeslagen.": "Saved.",
    "Wachtwoord wijzigen": "Change password",
    "Huidig wachtwoord": "Current password",
    "Aan": "On",
    "Nieuwe herstelcodes maken": "Create new recovery codes",
    "Je oude herstelcodes werken daarna niet meer. Bevestig met een code uit je app.": (
        "Your old recovery codes will stop working. Confirm with a code from your app."
    ),
    "Codes maken": "Create codes",
    "Synchroniseer je taken met elke app die CalDAV ondersteunt, zoals Apple Herinneringen, Thunderbird of DAVx⁵. Gebruik per apparaat een eigen app-wachtwoord. Taken verschijnen in de lijst Schrift.": (
        "Sync your tasks with any app that supports CalDAV, such as Apple Reminders, Thunderbird "
        "or DAVx⁵. Use a separate app password for each device. Tasks appear in the list Schrift."
    ),
    "Server-URL": "Server URL",
    "Zo stel je het in op een iPhone of iPad": "How to set it up on an iPhone or iPad",
    "Maak hieronder een app-wachtwoord.": "Create an app password below.",
    "Open Instellingen › Apps › Herinneringen › Herinneringen-accounts › Voeg account toe › Andere › Voeg CalDAV-account toe.": (
        "Open Settings › Apps › Reminders › Reminders Accounts › Add Account › Other › "
        "Add CalDAV Account."
    ),
    "Vul bij Server alleen de domeinnaam in, en je gebruikersnaam en het app-wachtwoord.": (
        "Enter only the domain name as Server, plus your username and the app password."
    ),
    "App-wachtwoorden": "App passwords",
    "laatst gebruikt": "last used",
    "nog niet gebruikt": "not used yet",
    "Intrekken": "Revoke",
    "Ingetrokken.": "Revoked.",
    "App-wachtwoord maken": "Create app password",
    "Nieuw app-wachtwoord": "New app password",
    "Naam van het apparaat": "Device name",
    "Kopieer het nu, het wordt hierna niet meer getoond.": "Copy it now, it won't be shown again.",
    "Geef een AI-assistent toegang tot je notities en taken. Clients met inloggen (OAuth), zoals claude.ai, koppel je via de server-URL. Andere clients, zoals Claude Code, gebruiken een token in de Authorization-header.": (
        "Give an AI assistant access to your notes and tasks. Clients that log in (OAuth), "
        "such as claude.ai, connect with the server URL. Other clients, such as Claude Code, "
        "use a token in the Authorization header."
    ),
    "Voorbeeld voor Claude Code:": "Example for Claude Code:",
    "Tokens": "Tokens",
    "Token maken": "Create token",
    "Nieuw token": "New token",
    "Naam van het token": "Token name",
    "Gekoppelde clients": "Connected clients",
    "Ontkoppelen": "Disconnect",
    "Ontkoppeld.": "Disconnected.",
    "Nog geen.": "None yet.",
    "Iedere gebruiker heeft eigen notities en taken. Nieuwe gebruikers kiezen bij de eerste keer inloggen een eigen wachtwoord en stellen tweestapsverificatie in.": (
        "Every user has their own notes and tasks. New users choose their own password and set "
        "up two-step verification when they first log in."
    ),
    "beheerder": "admin",
    "moet nog inloggen": "hasn't logged in yet",
    "2FA nog niet ingesteld": "2FA not set up yet",
    "Wachtwoord resetten": "Reset password",
    "Gebruiker en notities verwijderen": "Delete user and notes",
    "Gebruiker toevoegen": "Add user",
    "Gebruiker verwijderd.": "User deleted.",
    "Tijdelijk wachtwoord voor {name}": "Temporary password for {name}",
    "Uitloggen": "Log out",
    "Je huidige wachtwoord klopt niet.": "Your current password is incorrect.",
    "Wachtwoord gewijzigd. Andere sessies zijn uitgelogd.": (
        "Password changed. Other sessions have been logged out."
    ),
    "Gebruik alleen letters, cijfers, punt, streepje of underscore.": (
        "Use only letters, digits, dot, dash or underscore."
    ),
    "Die gebruikersnaam bestaat al.": "That username already exists.",
}
