# Manual de usuario — Editor de sonido WPC

*[English version](manual.en.md)*

Este editor abre las ROM de sonido de una máquina Williams / Bally WPC, te deja escuchar y
sustituir sus sonidos y te devuelve imágenes parcheadas, listas para PinMAME o para grabar
en EPROM. Todo ocurre en tu ordenador: la página del navegador habla con un pequeño
programa local y nada se sube a internet.

---

## 1. Arrancar el editor

- **Desde el código:** `python -m wpcide`. Se abre `http://127.0.0.1:8765/` en el navegador.
- **Versión empaquetada:** abre **`dist\EditorWPC\EditorWPC.exe`**. Aparece una ventana negra
  con la dirección del editor y, a continuación, el navegador. **Deja esa ventana abierta**
  mientras trabajes: si la cierras, el editor se apaga.

Si ya hay un editor abierto, al lanzar otro se abre el navegador en el que ya estaba. Si
algo impide arrancar, la ventana muestra el error y espera a que pulses Enter.

Botón de idioma (bandera) arriba a la derecha: español / inglés.

---

## 2. Cargar las ROM

1. Elige el tipo de placa: **Anterior a DCS (1990-1993)** — tres zócalos, U18, U15 y U14 —
   o **DCS (1993-1998)** — siete zócalos, U2 a U8.
2. Arrastra los ficheros al recuadro, de cualquiera de estas formas:
   - **el zip del romset de PinMAME** entero (cada fichero encuentra su zócalo por su CRC);
   - **la carpeta extraída** completa: se ignoran solos el README, el PDF de licencia, etc.;
   - **un fichero sobre cada zócalo**, si el nombre no deja claro cuál es.
3. Cada zócalo se pone verde cuando la imagen coincide con un juego conocido. Con todas
   cargadas, el editor identifica la máquina y abre su vista.

Juegos conocidos ahora: *Twilight Zone* (sonido L-1 y L-2) y *Star Trek: The Next
Generation* (sonido L-1 y especial LX-7). Una imagen que no coincide se marca en rojo: el
mapa del editor se construyó sobre esos volcados exactos y no se aplicaría a otros.

**Buscar la máquina:** el buscador de la pantalla de carga abre la ficha del juego en IPDB,
donde están publicadas las imágenes oficiales con permiso de Planetary Pinball Supply. El
editor no descarga nada por sí mismo: bajas el fichero en tu navegador y lo sueltas aquí.

---

## 3. Juegos anteriores a DCS (Twilight Zone)

### 3.1 El mapa de chips

Cada ROM se dibuja como su chip, con sus páginas de 32 KB y cada bloque de sonido en su
posición real. Azul es la voz (HC55516 / CVSD), naranja las muestras DAC, verde lo que ya
has editado; cada bloque lleva la silueta de su volumen.

Pulsa un bloque para ver sus datos (chip, posición, tamaño, duración, qué comandos lo
disparan) y escucharlo.

### 3.2 Sustituir un bloque por un WAV

1. Con el bloque elegido, **Sustituir** y elige tu WAV.
2. El informe compara lo que dura tu audio con lo que admite el bloque. **El tamaño de un
   bloque no puede cambiar**, así que eliges cómo encajarlo:
   - **Recortar** — conserva una ventana del tamaño del bloque; el deslizador elige cuál.
   - **Bucle** — repite el audio hasta llenar el bloque.
   - **Velocidad** — lo estira o encoge para que encaje exacto (cambia el tono).
   - **Automático** — ajusta la velocidad si falla por poco; si no, recorta o repite.
3. **Escuchar cómo quedaría** reproduce el audio ya codificado y decodificado: lo que de
   verdad sonará en la máquina, con las pérdidas del códec.
4. **Aplicar**. **Original** te deja comparar en cualquier momento; **Restaurar** deshace.

### 3.3 Anotar

Cada bloque admite una nota, una categoría (voz, efecto, música, ambiente, jingle…), un
color propio y una marca "para editar". Las notas se guardan junto a tus datos y se pueden
exportar e importar en JSON.

### 3.4 La música (pestaña Música)

La música de estas placas no está grabada: son notas que el programa envía al chip FM
YM2151. El editor **emula la placa** para oírla.

- **Escuchar** genera la música de un comando (la primera vez tarda unos segundos; luego
  queda guardada). **Generar todas** las prepara de una vez, con barra de progreso.
- Cada música admite las mismas anotaciones que un bloque.

**Sustituir una música por un WAV.** El chip FM no puede tocar audio, así que el comando
pasa a reproducir tu grabación con un chip de muestras:
- **DAC** — calidad alta (8 bits a 11 kHz), muy poco espacio libre;
- **CVSD** — calidad de voz, unas seis veces más espacio.

El audio ocupa bloques existentes: el editor propone primero los que nadie usa y te dice
qué deja de sonar con cada uno.

**Sustituir una música por un MIDI.** El MIDI se compila a la propia música FM del juego:
1. Carga el `.mid`. Verás las **partes** de la música original, cada una con su instrumento.
2. Para cada parte puedes elegir **uno de los 104 instrumentos** del juego y oírlo con ▶.
3. Ordena los **canales MIDI por prioridad**: los primeros nunca pierden notas; el resto
   ocupa los huecos (el chip tiene 8 canales y algunas partes van a dos voces).
4. Elige el **bloque prestado** donde irán las notas. Si no cabe, decide si **recortar el
   final** o **quitar los canales de menos prioridad**. Si sobra sitio no hace falta
   rellenarlo: la canción se repite sola.
5. **Escuchar cómo quedaría** (unos 30 s de espera) y **Aplicar**.

Ten en cuenta que algunos comandos comparten programas: si cambias uno, el informe te dice
qué otros cambian también.

### 3.5 Descargar

**Descargar ROM** baja un zip con las tres imágenes parcheadas, del mismo tamaño que las
originales y con el **checksum de arranque corregido** (sin esa corrección la placa daría
error al encender), más un README con cada cambio.

---

## 4. Juegos DCS (Star Trek: The Next Generation)

### 4.1 La lista de pistas

Cada pista es lo que suena cuando la máquina la pide. Se filtran por canal — el **0 es la
música**, el 1, 2 y 3 voces y efectos — y se buscan por comando (`01 76`) o por tu nota. El
icono ⟳ indica que la pista se repite en bucle.

A la derecha están **los siete chips** con cada trozo de audio en su sitio real. Al elegir
una pista se iluminan en naranja los trozos que reproduce; al pulsar un trozo se elige una
pista que lo usa.

### 4.2 Sustituir una pista por tu audio

1. Elige la pista y pulsa **Sustituir por un audio** (WAV, MP3 o lo que tu navegador sepa
   leer).
2. Aparece tu audio dibujado a todo lo ancho, con una **ventana del tamaño del hueco** (lo
   que dura la pista original) colocada donde empieza el sonido.
   - **Arrastra la ventana** por la onda: al soltarla suena lo que queda dentro. Con
     **Repetir mientras la muevo** sigue sonando en bucle y te sigue.
   - **Tira de los bordes** para ampliar o reducir la zona, o escribe inicio y fin.
   - **Mantener el tamaño del hueco** hace que los bordes muevan la ventana entera.
3. **Ajuste al hueco**, si tu selección no dura lo mismo que la pista:
   - **Sin ajustar** — la pista nueva dura lo que tu selección;
   - **Acelerar o frenar conservando el tono** — la voz no se vuelve aguda;
   - **Acelerar o frenar cambiando el tono** — como una cinta más rápida.
   La selección ya suena a la velocidad final cuando la escuchas.
4. **Igualar el volumen** al de la pista original viene activado.
5. **Escuchar en la ROM** (unos 12 s) codifica tu audio en DCS y te pone lo que tocará la
   máquina. **Aplicar** lo deja hecho; **Restaurar la pista original** lo deshace.

La pista se **reprograma** alrededor de tu audio: conserva su volumen y cómo baja la música
mientras suena, y los audios que comparte con otras pistas no se tocan.

**Espacio.** La ROM tiene que seguir siendo de siete chips para caber en la placa. El
editor prueba de la mejor calidad hacia abajo y usa la más alta que cabe; si ni a la mínima
cabe, te lo dice. Sustituir una pista libera los audios que solo usaba ella.

### 4.3 Descargar

**Descargar ROM** baja los siete chips con los nombres del romset de PinMAME y un README.

---

## 5. Probar antes de grabar

1. **PinMAME**: mete las imágenes descargadas en el zip del romset (por ejemplo
   `sttng_l7.zip`, junto a la ROM de la CPU) y arráncalo. Avisará de que los CRC no
   coinciden — es normal en una ROM modificada — y con una tecla continúa.
2. Para oír un sonido concreto, el menú de servicio de la máquina tiene un **test de
   sonido** (en PinMAME las teclas de la puerta de monedas son 7 salir, 8 menos, 9 más,
   0 entrar). Ese test solo ofrece una selección de sonidos.
3. **En la máquina real**: graba las imágenes en EPROM del mismo tipo (27C010 / 27C040) con
   un programador (TL866, T48…). Hazlo bajo tu responsabilidad y guarda siempre las
   originales.

---

## 6. Problemas frecuentes

| Qué pasa | Qué hacer |
| --- | --- |
| Abro el exe y no hace nada | Abre `dist\EditorWPC\EditorWPC.exe`, no el de `build\`. |
| "Ese comando no produjo música" | Mira el mensaje: si dice que no hay conexión, el editor se cerró; arráncalo y vuelve a cargar las ROM. |
| Un zócalo en rojo | Esa imagen no es de ningún juego conocido (otra revisión, o un fichero dañado o vacío). |
| No suena la música FM | Falta VGMPlay en `tools/vgmplay/` (ver `tools/README.md`). |
| No abre un juego DCS | Falta DCS Explorer en `tools/dcsexplorer/`. |
| "No cabe" | Usa menos audio, menos calidad (DCS lo hace solo) u otro bloque más grande. |

Tus datos (notas, música ya generada, ajustes) están en `%LOCALAPPDATA%\EditorWPC`.
