"""Plain-language text and research reference numbers for the individual shadow page (no heavy imports: the Pages job uses it)."""

ORDER = ["R1", "R2", "R3", "R4", "R5", "R7", "R10", "R12", "R13", "R37"]

# plain-language text for the page (Spanish)
DESCR = {
    "R1": "Antes de empezar, si alguno de los jugadores suele terminar muy por debajo de la línea y es antes de las 4 PM (Cuba).",
    "R2": "Tras el set 1 si fue una paliza (6+ de diferencia) y algún jugador suele terminar muy por debajo de la línea.",
    "R3": "Masters Rusia: algún jugador de pocos puntos y el modelo no se aleja del mercado.",
    "R4": "Antes de empezar, partido ruso donde algún jugador suele terminar muy por debajo de la línea.",
    "R5": "Tras 2 a 4 sets (no China): último set muy desigual, pocos puntos por jugar y el modelo ve valor.",
    "R7": "Masters Rusia con el partido ya avanzado (13+ min): el modelo ve más probabilidad de Under que el mercado.",
    "R10": "Tras 1 a 4 sets con el último set corto (15 puntos o menos) y algún jugador de pocos puntos.",
    "R12": "Tras 2 a 4 sets (no China): pocos puntos por jugar, línea cerrada y ventaja del modelo.",
    "R13": "Tras 2 a 4 sets (no China): totales por jugador muy separados y ventaja del modelo.",
    "R37": "Antes de las 12 PM (Cuba): jugadores de pocos puntos y el modelo no se aleja del mercado.",
}

# ROI of each rule in the research tests (confirmation window / first blind window), percent. Live is expected to be lower.
REF = {"R1": (43.6, 26.0), "R2": (42.5, 49.0), "R3": (43.2, 30.2), "R4": (35.8, 37.0), "R5": (29.7, 20.2), "R7": (28.5, 29.1),
       "R10": (30.2, 14.1), "R12": (29.5, 22.3), "R13": (29.2, 24.7), "R37": (29.9, 23.1)}
