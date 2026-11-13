# Forma de trabajar en TaxiRoute

Estas instrucciones recogen las preferencias de trabajo y las decisiones del proyecto. Las peticiones actuales de Iván tienen prioridad sobre este documento. Para retomar el proyecto, usa MAPA_CONTEXTO.md como índice y verifica el estado real antes de actuar. Consulta README.md, ALCANCE_FASE_3.md, DISENO_CUENTAS.md y DISENO_RECUPERACION.md según la tarea; los apartados históricos describen la situación de su fecha, no necesariamente la actual.

## Colaboración y aprendizaje

- Trabaja en español y explica de forma didáctica qué haces, por qué, qué resultado esperas y qué has comprobado. Cuando ayude al aprendizaje, explica los comandos y los conceptos de Python, como clases, objetos, funciones y atributos.
- Comparte avances mientras trabajas. Cuando sea viable, muestra los cambios y pruebas en el editor o en la terminal visible; si no puedes, dilo y ofrece comandos reproducibles. No afirmes que una ventana o prueba se ha visto si no lo has verificado.
- Toma decisiones rutinarias con criterio y continúa dentro del alcance autorizado. Presenta alternativas, ventajas y dificultades cuando la decisión cambie de forma material el funcionamiento, el coste, la arquitectura, la pérdida de datos o la experiencia. Explica tu recomendación y acuerda la decisión cuando sea necesario.
- Usa agentes en tareas independientes cuando aporten valor y esté permitido. Reparte alcances claros, evita ediciones simultáneas de los mismos archivos, revisa sus resultados y explica lo relevante. Usa o instala habilidades útiles dentro de las autorizaciones y restricciones aplicables; no trates su instalación como prueba de calidad.
- Mantén una comunicación natural y personal. En documentación, pull requests y comprobaciones evita atribuciones como «Iván ha realizado»; expresa el trabajo de forma natural, por ejemplo «se ha comprobado», sin inventar autorías o pruebas.

## Criterios de desarrollo

- Respeta las fases y el enunciado. Conserva lo ya entregado y diferencia requisitos obligatorios de mejoras, decisiones aprobadas y propuestas pendientes. Actualiza la documentación cuando una decisión o un estado cambien de forma relevante.
- Diseña pensando en uso local, interfaz responsive, accesibilidad y seguridad. El acceso remoto desde varias ubicaciones está aprobado como evolución posterior; no lo presentes como implementado ni lo habilites sin acordar su diseño.
- Mantén las tarifas de una carrera fijadas al iniciarla; los cambios se aplican a carreras nuevas. Las cuentas, los permisos, la asociación con conductores y las carreras simultáneas deben comprobarse en el servidor, no confiarse a datos que envíe el navegador.
- En fase 3 se conserva el historial CSV y se ha decidido usar SQLite para cuentas y el diseño de recuperación; la migración del historial queda para la fase 4. Conserva los datos históricos sin atribuirles un conductor inventado.
- Se acordó un punto de control inicial cada cinco segundos y al producirse las transiciones pertinentes. Tras una interrupción, recuperar hasta el último punto válido, no sumar automáticamente el tiempo no registrado y pedir revisión. No anuncies recuperación, durabilidad, persistencia o integración como terminadas sin pruebas que lo acrediten.
- Separa pruebas con datos y bases temporales de datos reales. No muestres ni incluyas en el repositorio contraseñas, PIN, tokens, claves privadas o credenciales OAuth. No envíes correos ni realices otras comunicaciones sin autorización explícita aplicable.
- Verifica los comportamientos relevantes, errores, permisos y concurrencia según el cambio. Ejecuta las comprobaciones necesarias y comunica los resultados reales y sus límites: una prueba de Flask con HTTPS simulado no equivale a una prueba de navegador ni acredita certificados, accesibilidad o uso remoto.
- Conserva los cambios existentes y evita sobrescribir trabajo ajeno. Los mensajes de commit del proyecto se redactan en español y describen el cambio. No reescribas historial publicado ni realices acciones irreversibles sin autorización aplicable.

## Estado a tener presente

Las fases 1 y 2 están integradas según el README. La fase 3 se ha cerrado como demostración académica local, con las limitaciones descritas en ESTADO_ENTREGA.md; hay móvil y tablet disponibles para una futura prueba física en la misma red. La fase 4 está en desarrollo en su rama. La web habitual anterior no debe darse por protegida ni la recuperación por terminada. Antes de retomar, verifica el estado real de la rama, los archivos y las pruebas; no supongas que los documentos históricos siguen actualizados.