# WPC IDE — Plan revisado

Oct 11, 2026 · @Fernando

## Visión y decisiones

El proyecto empieza por el audio de una máquina pre-DCS, porque es la pieza sin herramientas públicas y sin ella el resto no tiene sentido.

- **Objetivo:** IDE/SDK para programar y corregir máquinas Williams/Bally WPC reales, en C sobre FreeWPC, con toolchain en Docker.
- **Proyecto personal.** A la comunidad se publica solo el IDE y las herramientas, nunca ROMs.
- **Importador de ROMs:** cada usuario carga las suyas; el sistema comprueba el hash, extrae, modifica y regenera la imagen.
- **Máquina piloto pre-DCS** (placa YM2151 + CVSD). Decidido: Twilight Zone (TZ), la más trabajada en FreeWPC. Otras candidatas que quedan fuera: Addams Family, Terminator 2, Funhouse, White Water.
- **Carpeta del proyecto:** una carpeta local (ahí se irá guardando todo).
- **STTNG queda para después:** es DCS y ya existen herramientas (DCS Explorer / DCS Encoder).
- **Extracción desde manuales PDF descartada:** las matrices ya están en FreeWPC y PinMAME, y los manuales suelen ser escaneos.

## Audio pre-DCS

La ROM de sonido es un programa 6809 más sus datos; el primer objetivo es la voz CVSD, por ser lo más abordable.

| Contenido | Chip | Dificultad | Notas |
| --- | --- | --- | --- |
| Voz | Harris HC55516 (CVSD) | Media | Modulación delta de 1 bit; decodificador documentado en MAME; escribir el codificador WAV → CVSD |
| Muestras DAC | DAC 8 bits | Baja | Más sencillas una vez localizadas |
| Música y efectos FM | Yamaha YM2151 | Alta | Son secuencias de registros, no grabaciones; requiere secuenciador o tracker propio |

**Ruta de ingeniería inversa**

1. Usar el driver de la placa de sonido WPC de MAME/PinMAME como documentación y banco de pruebas.
2. Desensamblar el programa 6809 y localizar la tabla de despacho de comandos (qué código hace sonar qué).
3. Localizar los bloques CVSD y extraerlos a WAV; si suenan bien, el formato está entendido.
4. Sustituir una frase, ajustar punteros y longitudes y probar en PinMAME.
5. Grabar la EPROM y probar en la máquina real.

## Roadmap

Cada fase termina con algo que funciona en la máquina real o en PinMAME.

1. **Audio pre-DCS:** extractor CVSD → WAV, codificador WAV → CVSD y sustitución de una frase en hardware real.
2. **Mapa de sonidos:** tabla código de comando → sonido, base del Mapeador de Sonido del IDE.
3. **Importador de ROMs:** verificación de hash, extracción y regeneración, empaquetado en Docker.
4. **Toolchain y código:** contenedor con gcc6809 y FreeWPC; botones Build y Play en VS Code.
5. **Conversor DMD:** imágenes a 128x32 con los niveles de gris del DMD (no solo 1 bit).
6. **Música FM:** secuenciador para el YM2151.
7. **Ampliación:** soporte DCS (STTNG) apoyado en DCS Encoder; apertura del repositorio a la comunidad.

## Recursos

| Recurso | Para qué sirve |
| --- | --- |
| FreeWPC | Código en C para la CPU WPC, con modo nativo y simulador; TZ es su máquina más completa |
| MAME / PinMAME | Emulación de la placa de sonido pre-DCS; su código documenta el hardware y sirve para probar ROMs modificadas |
| [DCS Explorer / DCS Encoder](https://github.com/mjrgh/DCSExplorer) | Explorar y crear ROMs DCS (títulos 1993–1998); la reproducción depende de APIs de Windows |
| IPDB, Planetary Pinball | Actualizaciones oficiales de software publicadas para operadores |
| Programador TL866 / T48 | Leer y grabar las EPROMs propias |

Pendiente: buscar en los foros de PinMAME y FreeWPC desensamblados previos del firmware de sonido pre-DCS.

Fuentes: [Digital Compression System – Wikipedia](https://en.wikipedia.org/wiki/Digital_Compression_System) · [The DCS System – Williams](https://planetarypinball.com/mm5/Williams/common/dcs.html)

## Siguientes pasos

- [x] Elegir la máquina piloto pre-DCS -- Twilight Zone
- [ ] Leer las EPROMs de sonido propias y anotar la revisión (de momento se trabaja con los volcados de IPDB, L-2, verificados por CRC)
- [x] Revisar el driver de sonido WPC en el código de MAME/PinMAME (`hc55516.c`, `wmssnd.c`)
- [ ] Buscar desensamblados previos en foros de PinMAME y FreeWPC
- [x] Escribir el extractor CVSD → WAV, y el de DAC (229 registros, 195 WAV)
- [x] Reinsertar un WAV en un bloque, con recorte, bucle, velocidad o automático
- [x] Mapa de sonidos por emulación: 124 comandos de música, 109 que disparan muestras; voz mapeada por el parámetro de tipo 7
- [x] Editor local (`python -m wpcide`): mapa de chips, anotaciones, música renderizada vía VGMPlay
- [x] Sustituir la música FM de un comando por un WAV (13 oct 2026): el comando pasa a tocar bloques CVSD; verificado en el 6809 emulado
- [x] Usar bloques DAC (opcode `$88`) como destino de un WAV de música, a elegir frente al CVSD
- [x] Corregir el checksum de arranque de la placa de sonido en toda imagen exportada
- [x] Probar una ROM parcheada en PinMAME: arranca como la original y suena a la velocidad calibrada
- [ ] Probar una ROM parcheada en la máquina real (grabar EPROMs)
- [x] Arreglar la pérdida de FIRQ del emulador propio: retardo de 3 µs al bajar la IRQ del YM2151, como PinMAME
- [x] Exportar la música FM a MIDI (desde las escrituras reales al YM2151)
- [x] Compilar un MIDI a los scripts de música de la ROM (`scripts/midi_to_rom.py`), verificado en ida y vuelta y en PinMAME
- [x] MIDI en el editor: partes, instrumento por parte (catálogo de 104), prioridad de canales, bloque prestado, escuchar, aplicar, restaurar
- [ ] Volumen por velocidad MIDI, batería MIDI → golpes DAC, nombres para los instrumentos
- [x] STTNG: ROMs de IPDB (juego LX-7, sonido L-1 y especial LX-7), CRC verificados y romsets `sttng_l7`/`sttng_x7` aceptados por PinMAME
- [x] DCS: DCS Explorer / Encoder (BSD-3) sobre STTNG — 482 pistas listadas, 472 WAV extraídos, una pista sustituida y verificada (470/472 idénticas)
- [x] DCS en el editor: zip de PinMAME, 482 pistas por canal, escuchar original/editada, recorte sobre la onda con ventana del tamaño del hueco, ajuste por velocidad (con o sin tono), escuchar en la ROM, aplicar, descargar
- [x] Incluir DCS Explorer y DCS Encoder (con su licencia) en el paquete EditorWPC, probado en el ejecutable
- [x] Probar un juego DCS parcheado en PinMAME: la narración nueva de 01 76 suena idéntica (correlación 0,99) y se oye bien
