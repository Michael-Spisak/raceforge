# KI-Prompt: Technisches Konzept für ein autonomes LEGO-Rennauto

## Rolle der KI

Du bist ein erfahrener Entwickler für autonome mobile Roboter mit Schwerpunkt auf:

- LEGO Mindstorms,
- LEGO-Technic-Fahrwerken,
- mechanischer Lenkung,
- Sensorik und Umfelderkennung,
- Raspberry Pi und Kameras,
- Embedded Software,
- autonomer Navigation in Innenräumen,
- zuverlässigen und kostengünstigen Prototypen.

Unterstütze uns bei der Entwicklung eines technisch realistischen Konzepts für ein autonomes LEGO-Rennauto. Das Ergebnis soll später als Grundlage für Konstruktion, Einkauf, Programmierung und Tests dienen.

## Vorgehensweise

Erstelle nicht sofort ein endgültiges Fahrzeugkonzept. Gehe zuerst so vor:

1. Analysiere die vorhandenen technischen Anforderungen.
2. Trenne verbindliche Vorgaben von unklaren Punkten.
3. Erkenne technische Widersprüche und Risiken.
4. Stelle uns gezielte technische Rückfragen.
5. Beginne mit höchstens 10 besonders wichtigen Fragen.
6. Gib bei den Fragen sinnvolle Auswahlmöglichkeiten an.
7. Frage nur Dinge, die für Fahrzeugarchitektur, LEGO-Konstruktion, Navigation, Sensorik, Elektronik, Software oder Budget relevant sind.
8. Frage nicht nach allgemeinen Projektrollen, Benotung oder organisatorischen Details, sofern diese die technische Lösung nicht direkt beeinflussen.
9. Triff keine stillen Annahmen. Kennzeichne ungeklärte Punkte mit **offen**.
10. Entwickle erst nach unseren Antworten konkrete Lösungsvarianten.

---

# Technische Aufgabenstellung

Es soll ein autonomes LEGO-Fahrzeug für ein Rennen in einem Gang der Schule entwickelt werden.

Das Fahrzeug muss:

- selbstständig fahren,
- ohne Fernsteuerung funktionieren,
- ohne Führungslinie navigieren,
- Kurven und den Verlauf des Ganges erkennen,
- andere Fahrzeuge und feste Hindernisse berücksichtigen,
- auf einem LEGO-Mindstorms-System basieren,
- mindestens einen für das Fahren relevanten Sensor oder Aktor über Mindstorms verwenden,
- während des Rennens ohne kabellose Verbindung funktionieren.

Ein reines Abfahren einer vorher ausgemessenen und fest einprogrammierten Strecke gilt nicht als ausreichende autonome Lösung. Das Fahrzeug soll seine Fahrentscheidungen zumindest teilweise anhand aktueller Sensordaten treffen.

---

# Verbindliche Vorgaben

## LEGO-Komponenten

Folgende Teile müssen aus LEGO bestehen:

- Räder,
- zentrale Lenkung beziehungsweise Lenkmechanik,
- mindestens ein LEGO-Mindstorms-Controller.

Die genaue Bedeutung von „zentrale Lenkung“ ist noch zu klären. Vermutlich ist eine lenkbare Achse oder eine mechanisch gekoppelte Fahrzeuglenkung gemeint und kein Differentialantrieb, bei dem nur über unterschiedliche Raddrehzahlen gelenkt wird.

## Mindstorms-Integration

- Mindestens ein Mindstorms-Controller muss eingebaut sein.
- Der Mindstorms-Controller darf nicht nur passiv mitgeführt werden.
- Er muss mindestens eine für das Fahren relevante Funktion übernehmen.
- Als Beispiele wurden das Ansteuern eines Lenkmotors und das Auslesen eines Abstandssensors genannt.
- Noch offen ist, ob sowohl ein Sensor als auch ein Aktor über Mindstorms laufen müssen oder ob eines davon ausreicht.
- Noch offen ist, welche Mindstorms-Version bereitgestellt wird.

## Bereitgestellte Ausstattung

- Jedes Team bekommt sicher einen Mindstorms-Kasten.
- Eventuell bekommt jedes Team einen zweiten Mindstorms-Kasten.
- Die bereitgestellten Kästen werden nicht vom Budget abgezogen.
- Bluetooth-Dongles sind für die Teams vorhanden.
- Der Verwendungszweck der Dongles muss geklärt werden, da beim Rennen keine kabellosen Verbindungen erlaubt sind.

## Erlaubte Zusatztechnik

Folgende Komponenten sind grundsätzlich erlaubt:

- Raspberry Pi,
- Kameras,
- nicht von LEGO stammende Elektromotoren,
- 3D-gedruckte Bauteile,
- zusätzliche käufliche Sensoren, sofern keine andere Regel dagegen spricht.

In der Schule steht ein 3D-Drucker zur Verfügung.

## Kommunikation

- Das Fahrzeug muss autonom arbeiten.
- Fernsteuerung ist verboten.
- Während des Rennens dürfen keine kabellosen Verbindungen aufgebaut oder verwendet werden.
- Es ist noch unklar, ob WLAN und Bluetooth lediglich deaktiviert oder vollständig entfernt werden müssen.
- Kabelgebundene Kommunikation zwischen Komponenten im Fahrzeug ist voraussichtlich möglich, aber noch nicht ausdrücklich bestätigt.

## Budget

- Das Budget beträgt 200 € pro Team.
- Von der Schule bereitgestellte Mindstorms-Komponenten zählen nicht zum Budget.
- Eigene Komponenten zählen mit ihrem realistischen Gebrauchtpreis zum Budget.
- Als Preisquellen können eBay und Willhaben verwendet werden.
- Ein eigener Mindstorms-Kasten müsste voraussichtlich mit seinem Gebrauchtwert eingerechnet werden.
- Noch offen ist, wie 3D-Druck-Material, Versand, Akkus, Kabel und Kleinteile verrechnet werden.

---

# Rennumgebung

## Strecke

- Das Rennen findet in einem Gang im Erdgeschoss der Schule statt.
- Es gibt keine Linie zur Spurführung.
- Es gibt keine beweglichen Hindernisse, abgesehen von den anderen teilnehmenden Fahrzeugen.
- Feste Gebäudestrukturen wie Wände, Türen, Nischen und Kurven können vorhanden sein.
- Die exakten Abmessungen und das genaue Streckenlayout sind noch nicht bekannt.
- Abhängig von der Geschwindigkeit der Fahrzeuge kann das Rennen über mehrere Runden gehen.

## Andere Fahrzeuge

- Alle Fahrzeuge sollen gleichzeitig starten.
- Kollisionen werden grundsätzlich nicht bestraft.
- Eine Störung anderer Fahrzeuge ist grundsätzlich erlaubt.
- Die technischen und sicherheitsbezogenen Grenzen dieser Störungen sind noch nicht definiert.
- Das eigene Fahrzeug sollte daher Kollisionen erkennen oder mechanisch verkraften können.

## Eingriffe

- Wenn ein Fahrzeug ein Problem hat, darf es nach dem Passieren der anderen Fahrzeuge manuell korrigiert werden.
- Welche Eingriffe erlaubt sind und an welcher Position das Fahrzeug wieder eingesetzt wird, ist noch offen.

---

# Zu entwickelnde technische Bereiche

Nach Beantwortung der offenen Fragen sollst du für jeden der folgenden Bereiche konkrete Lösungsvorschläge entwickeln.

## Fahrzeugarchitektur

- Anordnung von Mindstorms-Controller, Raspberry Pi, Sensoren, Motoren und Akku
- Schwerpunkt und Gewichtsverteilung
- Modularer Aufbau
- Zugänglichkeit von Akku, Kabeln und Not-Aus
- Schutz vor Kollisionen
- Wartungs- und Reparaturfreundlichkeit

## LEGO-Fahrwerk

- Geeignete LEGO-Technic-Rahmenkonstruktion
- Radstand und Spurbreite
- Auswahl der LEGO-Räder
- Bodenfreiheit
- Stabilität bei höherer Geschwindigkeit
- Vermeidung von Verwindung
- Befestigung von Nicht-LEGO-Komponenten am LEGO-Fahrwerk

## Lenkung

Prüfe insbesondere:

- gelenkte Vorderachse,
- Ackermann-Lenkung,
- LEGO-Lenkmotor oder externer Servomotor,
- mechanische Begrenzung des Lenkwinkels,
- Ermittlung der Lenkposition,
- automatische Rückstellung auf Geradeausfahrt,
- Lenkspiel und Präzision,
- Verhalten bei Kollisionen.

Die Lenkmechanik muss aus LEGO bestehen. Ob der Lenkmotor ebenfalls von LEGO sein muss, ist offen.

## Antrieb

Vergleiche:

- LEGO-Mindstorms-Motor als Antrieb,
- externer Gleichstrommotor,
- externer Getriebemotor,
- ein oder zwei Antriebsmotoren,
- Front-, Heck- oder Allradantrieb,
- LEGO-Getriebe gegenüber externem Getriebe,
- Übersetzung für Geschwindigkeit gegenüber Drehmoment,
- Messung von Radumdrehungen und Geschwindigkeit.

Berücksichtige dabei das Budget und die mechanische Integration in LEGO.

## Sensorik

Prüfe sinnvolle Kombinationen aus:

- LEGO-Ultraschallsensor,
- externem Ultraschallsensor,
- Time-of-Flight-Abstandssensor,
- LiDAR,
- Kamera,
- Gyroskop beziehungsweise IMU,
- Raddrehgeber,
- Kontaktsensor beziehungsweise Bumper,
- seitlichen Abstandssensoren.

Die Navigation darf nicht ausschließlich auf einer vorher eingespeicherten Streckenlänge basieren. Bewerte deshalb, welche Sensoren tatsächlich zur Erkennung von Wänden, Kurven, Hindernissen und anderen Fahrzeugen beitragen.

## Autonome Navigation

Untersuche geeignete Ansätze:

- Wandfolgen mit seitlichen Abstandssensoren,
- Zentrieren zwischen zwei Wänden,
- Erkennung von Kurven anhand der Wandgeometrie,
- visuelle Navigation mit Kamera,
- lokale Hindernisvermeidung,
- Kombination aus Kamera und Abstandssensoren,
- Regelung mit Gyroskop und Raddrehgebern,
- SLAM oder vereinfachte lokale Kartierung.

Einfaches zeitgesteuertes Fahren oder ausschließlich fest programmierte Fahrsequenzen sollen nicht als Hauptlösung vorgeschlagen werden.

## Steuerungsarchitektur

Prüfe mindestens diese Varianten:

1. Nur Mindstorms-Controller
2. Raspberry Pi als Hauptrechner und Mindstorms für Lenkung oder Sensorik
3. Mindstorms als Hauptsteuerung mit zusätzlichem Raspberry Pi für Kameraverarbeitung
4. Zwei Mindstorms-Controller mit kabelgebundener Kommunikation

Für jede Variante sollen Aufgabenverteilung, Vorteile, Nachteile, Komplexität, Zuverlässigkeit und Kosten dargestellt werden.

## Software

Das spätere Konzept soll unter anderem enthalten:

- Zustandsautomat für Start, Geradeausfahrt, Kurve, Hindernis, Kollision und Fehlerfall
- Sensorfusion
- Lenkregelung
- Geschwindigkeitsregelung
- Hinderniserkennung
- Verhalten bei Sensorausfall
- Logging auf dem Fahrzeug
- Kalibrierung vor dem Start
- Testbarkeit einzelner Komponenten

## Stromversorgung

Zu klären und zu planen sind:

- Versorgung des Mindstorms-Controllers,
- Versorgung des Raspberry Pi,
- Versorgung externer Motoren,
- gemeinsame oder getrennte Akkus,
- Spannungswandler,
- Absicherung,
- Laufzeit,
- Ladezeit,
- sichere Befestigung,
- gut erreichbarer Hauptschalter beziehungsweise Not-Aus.

## 3D-Druck

Sinnvolle Einsatzbereiche können sein:

- Halterungen für Kamera und Sensoren,
- Adapter zwischen LEGO und Nicht-LEGO-Komponenten,
- Motorgehäuse,
- Kabelhalter,
- Schutzteile,
- Akkuhalterung.

Tragende Komponenten der vorgeschriebenen LEGO-Lenkmechanik dürfen nicht ohne ausdrückliche Freigabe durch 3D-Druckteile ersetzt werden.

---

# Technische Hauptrisiken

Berücksichtige bei der späteren Konzeption insbesondere:

- unklarer Mindstorms-Typ,
- unbekanntes Streckenlayout,
- schwankende Lichtverhältnisse für Kameras,
- reflektierende oder absorbierende Oberflächen für Abstandssensoren,
- Lenkspiel bei LEGO-Technic-Konstruktionen,
- zu hohes Fahrzeuggewicht,
- instabile Stromversorgung des Raspberry Pi,
- unterschiedliche Spannungen von LEGO- und Fremdkomponenten,
- Kabel, die in Räder oder Lenkung geraten,
- unzureichende Rechenleistung,
- lange Startzeit des Raspberry Pi,
- Kollisionen mit anderen Fahrzeugen,
- blockierte Sensoren,
- Funkmodule, die versehentlich aktiv bleiben,
- Überschreitung des Budgets,
- zu komplexe Lösung für die verfügbare Projektzeit.

---

# Erwartete erste Antwort

Verwende für deine erste Antwort ausschließlich folgende Struktur:

## Technisches Verständnis

Fasse die technische Aufgabe in höchstens fünf Sätzen zusammen.

## Sichere Vorgaben

Liste nur die bereits eindeutig festgelegten technischen Regeln auf.

## Technische Konflikte

Nenne Widersprüche oder Unklarheiten, die eine konkrete Konstruktion aktuell verhindern.

## Wichtigste technische Fragen

Stelle maximal zehn priorisierte Fragen. Konzentriere dich zuerst auf:

1. genaue Mindstorms-Version,
2. Bedeutung der zentralen LEGO-Lenkung,
3. Aufgaben des Mindstorms-Controllers,
4. Fahrzeuggröße und Gewicht,
5. bekannte Eigenschaften der Strecke,
6. erlaubte Navigation und Sensoren,
7. Motor- und Akkuvorgaben,
8. erlaubte Fremdteile,
9. Funkverbot und interne Kommunikation,
10. Grenzen für Kollisionen und Störtechnik.

Nutze Antwortoptionen, wenn sinnvoll.

## Erste Lösungsrichtungen

Nenne höchstens drei grundsätzlich mögliche technische Architekturen. Beschreibe diese noch nicht vollständig, sondern jeweils nur in zwei bis drei Sätzen.

Beginne jetzt mit der technischen Klärung. Stelle noch keinen endgültigen Bauplan und keine endgültige Einkaufsliste auf.
