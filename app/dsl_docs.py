# dsl_docs.py
"""Встроенная справка по EchoScript (HTML для окна «Справка» в студии скриптов)."""

CSS = """
<style>
 body { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #e8e6f0; }
 h1 { font-size: 22px; color: #ffb347; margin: 4px 0 6px 0; }
 h2 { font-size: 16px; color: #6ae3ff; margin: 18px 0 6px 0; border-bottom: 1px solid #333; }
 h3 { font-size: 13px; color: #ff8fb1; margin: 12px 0 4px 0; }
 code { font-family: Consolas, 'Cascadia Mono', monospace; color: #ffd28a; }
 pre { font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px; background: #15131f;
       color: #e9e3ff; padding: 8px; border-radius: 6px; }
 td { padding: 3px 10px 3px 0; vertical-align: top; }
 .k { color: #ffd28a; font-family: Consolas, monospace; white-space: nowrap; }
 .m { color: #9c96b3; }
</style>
"""

SECTIONS = [
("Что это", """
<h1>EchoScript — язык тем ECHOES</h1>
<p>EchoScript позволяет написать <b>свою тему целиком</b>: любые виджеты (кнопки, прогресс, обложку,
списки), их поведение (мышь, анимации, физика) и визуализаторы, реагирующие на звук. Скрипт
пересобирается <b>на лету</b>: правите код — через полсекунды изменения уже на экране, а состояние
виджетов (позиции, накопленные частицы) сохраняется.</p>
<p>Файл скрипта (<code>.echo</code>) состоит из трёх частей:</p>
<pre>theme { name: "Моя тема", bg: "#0b0a14", accent: "#ffb347" }   // оформление

widget Pulse {                    // свой виджет
  on draw {
    fill(theme.accent)
    circle(w / 2, h / 2, 20 + audio.bass * 60)
  }
}

scene {                           // что и где на экране
  add Pulse { x: 0.4, y: 0.3, w: 0.2, h: 0.3 }
}</pre>
"""),

("Без кода", """
<h2>Собрать тему мышью</h2>
<p>На сцене нажмите <b>«Изменить тему»</b> — откроется конструктор, и виджеты можно сразу двигать. <b>«Готово»</b> закрывает его (всё сохраняется само).</p>
<ol>
<li><b>Файл ▾ → Новый</b>: «Пустой холст», «Готовый набор» (обложка, название, прогресс, кнопки,
громкость, спектр, список треков) или «Из темы плеера» — цвета, шрифт и фон-картинка/видео
из любой темы, в том числе из конструктора тем.</li>
<li>Вкладка <b>«Конструктор»</b>: перетащите блок из списка на сцену (или щёлкните по нему). У каждого блока — живое превью. Есть фон со свечением, картинка/GIF,
видео, фигуры, текст, название трека, обложка, спектр, волна, круговой спектр, искры на бит,
кнопки, прогресс, громкость, список треков.</li>
<li><b>«Двигать»</b>: щёлкните по виджету и тяните его или его углы. Края прилипают к сетке и
к другим виджетам (Alt — без прилипания), Shift на углу сохраняет пропорции, стрелки сдвигают
(с Shift — крупнее), Delete удаляет, Esc снимает выделение, Ctrl+Z / Ctrl+Y (или ↶ ↷) отменяют и повторяют.</li>
<li><b>Несколько виджетов</b>: Ctrl+клик добавляет или убирает виджет из выделения, рамка мышью по пустому
месту (или по фону) выделяет всё, что в неё попало, Ctrl+A выделяет всё. Выделенное двигается вместе.
Можно выровнять по краям или центру, распределить с равными промежутками, сделать одной ширины или высоты.
<b>«Сгруппировать»</b> задаёт виджетам общее свойство <code>group</code>: потом щелчок по любому из них
выделяет всю группу.</li>
<li>Под блоками — <b>настройки</b> выбранного виджета: цвета, файл картинки или видео (…), размер шрифта,
режим кнопки и так далее, а также слой выше/ниже, копия, удаление и переход к его коду.</li>
<li><b>События</b> (ниже свойств) задают, что делает виджет по клику, нажатию, колесу, на удар в музыке,
при смене трека, наведении курсора: пауза, следующий трек, громкость, перемотка, экспорт или свой код.
В коде это свойство вида <code>on_click: fn() { next() }</code> в строке <code>add</code>, и оно работает
вдобавок к <code>on click</code> самого виджета.</li>
</ol>
<h2>Фигуры</h2>
<p>«Фигура»: прямоугольник, круг, кольцо, звезда (<code>sides</code> лучей), многоугольник (<code>sides</code> сторон),
шестиугольник, треугольник. «Векторный контур»: любая форма в синтаксисе SVG (свойство <code>d</code>, как в
иконках и логотипах). «Живая капля» колышется и дышит от музыки. У всех есть пульсация, вращение,
градиент и контур.</p>
<h2>Импорт из других тем</h2>
<p><b>Файл ▾ → Импорт виджетов из скрипта</b>: выберите скрипт и отметьте виджеты. Они перенесутся вместе
с нужными им функциями, цветами темы и (по желанию) расстановкой. <b>Импорт слоёв и фона из темы
конструктора</b> переносит цвета, фон (картинку или видео) и слои: картинки, GIF, видео, тексты, фигуры.</p>
<p>Всё, что делается мышью, сразу записывается в код скрипта. Его можно дописать руками, и
расстановка продолжит работать. Двигать мышью можно виджеты, добавленные отдельной строкой
<code>add</code>, а созданные в цикле помечены «(в цикле)».</p>
<h2>Скрипты и темы</h2>
<p>Каждый ваш скрипт появляется в списке тем в разделе <b>«МОИ СКРИПТЫ»</b> как «◈ Название».
В <b>«Файл ▾»</b> есть копия, переименование, экспорт в файл <code>.echo</code> (можно отправить другу),
импорт, «Взять цвета из темы» и удаление. Если поправить встроенный Loom, правки сохранятся
как ваша копия, а «Сбросить к встроенной версии» вернёт оригинал. Кнопка <b>⧉</b> или пункт
<b>«Файл ▾ → Студия в отдельном окне»</b> переключает режим: панель в плеере или отдельное окно.
Выбор запоминается.</p>
"""),

("Синтаксис", """
<h2>Синтаксис</h2>
<h3>Комментарии, переменные, присваивание</h3>
<pre>// однострочный     /* многострочный */
let x = 10           // объявить переменную (точка с запятой не нужна)
x = x + 1
x += 2   x -= 1   x *= 3   x /= 2</pre>
<h3>Значения</h3>
<table>
<tr><td class=k>12  3.5  1e3</td><td>числа</td></tr>
<tr><td class=k>"текст"  'текст'</td><td>строки (<code>\\n</code>, <code>\\t</code>, <code>\\"</code>); <code>+</code> склеивает с чем угодно</td></tr>
<tr><td class=k>true false nil</td><td>логические значения и «пусто»</td></tr>
<tr><td class=k>[1, 2, 3]</td><td>список: <code>a[0]</code>, <code>a[-1]</code>, <code>a.length</code>, <code>a.first</code>, <code>a.last</code></td></tr>
<tr><td class=k>{name: "x", "k": 1}</td><td>словарь: <code>d.name</code> или <code>d["k"]</code>; нет ключа — <code>nil</code></td></tr>
</table>
<h3>Операторы (по возрастанию приоритета)</h3>
<pre>cond ? a : b        тернарный
a or b   a and b   not a
==  !=  <  <=  >  >=
a..b                диапазон чисел a, a+1, …, b-1 (для циклов)
+  -   *  /  %   **  (деление на 0 даёт 0, а не ошибку)</pre>
<h3>Условия и циклы</h3>
<pre>if level > 0.8 { … } elif level > 0.4 { … } else { … }

for i in 0..64 { … }            // по числам
for item in list { … }          // по элементам
for i, item in list { … }       // с номером
for key, value in dict { … }    // по словарю
while x < 10 { x += 1 }
break   continue</pre>
<h3>Функции</h3>
<pre>fn smoothstep(e0, e1, x = 0.5) {
  let t = clamp((x - e0) / (e1 - e0))
  return t * t * (3 - 2 * t)
}
smoothstep(0, 1, 0.3)
smoothstep(0, 1, x: 0.3)        // именованный аргумент
let twice = fn(v) { return v * 2 }   // анонимная функция (замыкание)</pre>
<p class=m>Переменные видны внутри функции; объявленные на верхнем уровне файла — во всём скрипте.
Внутри виджета имена ищутся так: локальные → поля виджета → глобальные.</p>
"""),

("Виджеты", """
<h2>Виджеты</h2>
<pre>widget Name {
  prop size = 40          // свойство: значение по умолчанию, можно задать в add
  state angle = 0         // состояние: живёт между кадрами (при горячей перезагрузке сохраняется)
  fn helper(a) { … }      // метод (вызывается как helper(1) или self.helper(1))
  on frame(dt) { angle += dt * 90 }
  on draw { … }           // рисование, координаты — внутри виджета: (0,0) — левый верхний угол
}</pre>
<h3>Встроенные поля каждого виджета</h3>
<table>
<tr><td class=k>x, y, w, h</td><td>положение и размер в пикселях (пересчитываются из долей экрана)</td></tr>
<tr><td class=k>z</td><td>слой: больше — выше (рисуется позже и первым получает мышь)</td></tr>
<tr><td class=k>visible</td><td>показывать ли виджет</td></tr>
<tr><td class=k>clip</td><td>обрезать рисование по рамке виджета (по умолчанию true; для свечения за краями — false)</td></tr>
<tr><td class=k>hover, pressed</td><td>курсор над виджетом / зажата кнопка мыши</td></tr>
<tr><td class=k>cache, cache_key</td><td>cache: true — виджет рисуется один раз и дальше копируется картинкой;
перерисуется, когда изменится cache_key (например <code>cache_key = track.title</code>)</td></tr>
<tr><td class=k>resolution</td><td>0.1…1 — рисовать в уменьшенную картинку и растягивать (дёшево для светящихся визуализаторов)</td></tr>
<tr><td class=k>name</td><td>имя экземпляра</td></tr>
</table>
<h3>Сцена и тема</h3>
<pre>theme { name: "Neon", bg: "#05040a", accent: "#ff3d81", accent2: "#3dd6ff",
        text: "#f4f4f8", muted: "#8a8aa0", font: "Segoe UI" }
// любые свои ключи тоже можно: theme.glow, theme.radius …

scene {
  add Pulse { x: 0.1, y: 0.1, w: 0.3, h: 0.3, z: 2, size: 60 }
  for i in 0..5 {                       // в сцене работает обычный код
    add Pulse { x: 0.1 + i * 0.15, y: 0.6, w: 0.12, h: 0.2 }
  }
}</pre>
<p>Координаты: число <b>от 0 до 1</b> — доля ширины/высоты окна; <b>больше 1</b> — пиксели;
отрицательные <code>x</code>/<code>y</code> отсчитываются от правого/нижнего края.</p>
"""),

("События", """
<h2>События (on …)</h2>
<table>
<tr><td class=k>init</td><td>один раз при запуске сцены</td></tr>
<tr><td class=k>frame(dt)</td><td>каждый кадр (~60 раз в секунду); dt — секунды с прошлого кадра</td></tr>
<tr><td class=k>draw</td><td>рисование</td></tr>
<tr><td class=k>press(x, y, button)</td><td>нажатие мыши (button: 1 — левая, 2 — правая)</td></tr>
<tr><td class=k>release(x, y, button)</td><td>отпускание</td></tr>
<tr><td class=k>click(x, y, button)</td><td>клик (нажали и отпустили почти на месте)</td></tr>
<tr><td class=k>drag(x, y, dx, dy)</td><td>перетаскивание после press (dx, dy — смещение)</td></tr>
<tr><td class=k>move(x, y)</td><td>движение курсора над виджетом</td></tr>
<tr><td class=k>enter / leave</td><td>курсор зашёл / ушёл</td></tr>
<tr><td class=k>wheel(delta, x, y)</td><td>колесо мыши (delta: +1 вверх, -1 вниз)</td></tr>
<tr><td class=k>beat(power)</td><td>удар в музыке (power 0…1)</td></tr>
<tr><td class=k>track(t)</td><td>сменился трек (t — то же, что track)</td></tr>
<tr><td class=k>key(name)</td><td>клавиша ("a", "space", "left", "right", "up", "down", "enter", "escape")</td></tr>
<tr><td class=k>resize(w, h)</td><td>изменился размер виджета</td></tr>
</table>
<p class=m>Параметры можно не объявлять: <code>on click { … }</code> тоже работает.
Координаты мыши — внутри виджета.</p>
"""),

("Звук и плеер", """
<h2>Звук: audio</h2>
<table>
<tr><td class=k>audio.fft</td><td>спектр: список из 64 полос 0…1 (от низких к высоким, логарифмически)</td></tr>
<tr><td class=k>audio.bass / mid / high</td><td>энергия низов / середины / верхов 0…1 (с автоусилением)</td></tr>
<tr><td class=k>audio.level</td><td>общая громкость 0…1</td></tr>
<tr><td class=k>audio.peak</td><td>пик с плавным спадом</td></tr>
<tr><td class=k>audio.beat, audio.beat_power</td><td>в этом кадре удар? и его сила</td></tr>
<tr><td class=k>audio.wave</td><td>форма волны: 128 значений -1…1</td></tr>
</table>
<h2>Трек: track</h2>
<p><code>track.title, artist, album, cover</code> (путь к картинке), <code>duration, position</code> (секунды),
<code>progress</code> (0…1), <code>loaded</code> (true, если трек выбран).</p>
<h2>Плеер: player, playlist</h2>
<p><code>player.playing, volume</code> (0…1), <code>shuffle, repeat</code> (0 — нет, 1 — все, 2 — один)<br>
<code>playlist.name, tracks</code> (список {title, artist, duration, cover}), <code>index</code> (играющий), <code>count</code></p>
<h2>Время и экран</h2>
<p><code>time</code> (секунды с запуска), <code>dt</code>, <code>frame</code>, <code>W, H</code> (размер всей сцены),
<code>mouse.x, mouse.y, mouse.down</code> (в координатах сцены), <code>theme</code> — словарь темы.</p>
<h2>Действия</h2>
<table>
<tr><td class=k>toggle() play() pause()</td><td>воспроизведение</td></tr>
<tr><td class=k>next() prev()</td><td>следующий / предыдущий трек</td></tr>
<tr><td class=k>seek(frac)</td><td>перемотать к доле трека 0…1</td></tr>
<tr><td class=k>set_volume(v)</td><td>громкость 0…1</td></tr>
<tr><td class=k>play_index(i)</td><td>включить i-й трек текущего плейлиста</td></tr>
<tr><td class=k>toggle_shuffle() cycle_repeat()</td><td>перемешивание / режим повтора</td></tr>
<tr><td class=k>export_fx()</td><td>открыть «Экспорт с эффектами» для играющего трека</td></tr>
</table>
"""),

("Рисование", """
<h2>Рисование (только в on draw)</h2>
<h3>Кисть и перо</h3>
<table>
<tr><td class=k>fill(color)  nofill()</td><td>заливка фигур (цвет или градиент)</td></tr>
<tr><td class=k>stroke(color, width = 1, cap = "round")</td><td>контур; cap: "round" | "flat" | "square"</td></tr>
<tr><td class=k>nostroke()</td><td>без контура</td></tr>
<tr><td class=k>alpha(a)</td><td>прозрачность всего, что рисуется дальше (медленнее, чем цвет с прозрачностью)</td></tr>
<tr><td class=k>blend(mode)</td><td>"normal", "add" (свечение), "screen", "multiply", "overlay", "lighten", "darken", "difference"</td></tr>
</table>
<h3>Фигуры</h3>
<table>
<tr><td class=k>rect(x, y, w, h, r = 0)</td><td>прямоугольник (r — скругление)</td></tr>
<tr><td class=k>circle(x, y, r)   ellipse(x, y, rx, ry)</td><td>круг / эллипс</td></tr>
<tr><td class=k>line(x1, y1, x2, y2)</td><td>отрезок</td></tr>
<tr><td class=k>lines([x1, y1, x2, y2, …])</td><td>много отрезков за один вызов (быстро)</td></tr>
<tr><td class=k>poly(points, closed = true)</td><td>многоугольник / ломаная; points — [x1, y1, x2, y2 …] или [[x, y], …]</td></tr>
<tr><td class=k>curve(points, closed = false, tension = 0.5)</td><td>плавная кривая через точки</td></tr>
<tr><td class=k>arc(x, y, r, a0, a1)</td><td>дуга, углы в градусах (0 — вправо, по часовой)</td></tr>
<tr><td class=k>ring(x, y, r1, r2)</td><td>кольцо</td></tr>
<tr><td class=k>star(x, y, r1, r2, n = 5, rot = 0)</td><td>звезда</td></tr>
<tr><td class=k>ngon(x, y, r, n = 6, rot = 0)</td><td>правильный многоугольник</td></tr>
<tr><td class=k>glow(x, y, r, color, strength = 1)</td><td>мягкое свечение (лучше с blend("add"))</td></tr>
<tr><td class=k>clear(color)</td><td>залить всё</td></tr>
</table>
<h3>Текст и картинки</h3>
<table>
<tr><td class=k>text(s, x, y, size = 14, align = "left", weight = 500, font = nil, maxw = 0)</td>
<td>y — середина строки; align: "left" | "center" | "right"; maxw — обрезать «…»</td></tr>
<tr><td class=k>textwidth(s, size = 14, weight = 500)</td><td>ширина текста</td></tr>
<tr><td class=k>image(path, x, y, w, h, r = 0)</td><td>картинка (обрезка по центру без искажений); возвращает false, если её нет.
GIF/WEBP проигрываются. Обложка трека: <code>image(track.cover, …)</code></td></tr>
<tr><td class=k>video(path, x, y, w, h, r = 0, rate = 1)</td><td>видео без звука, по кругу (MP4, WEBM, MOV…);
rate — скорость. Декодер сам останавливается, когда видео перестают рисовать</td></tr>
<tr><td class=k>path(d, x = 0, y = 0, w = 0, h = 0)</td><td>векторный контур SVG (M L H V C S Q T A Z).
Если заданы w и h, он вписывается в прямоугольник с сохранением пропорций. Пример:
<code>path("M12 2 L22 22 H2 Z", 0, 0, w, h)</code></td></tr>
</table>
<h3>Трансформации</h3>
<p><code>save()</code> / <code>restore()</code> — запомнить и вернуть состояние; <code>translate(x, y)</code>,
<code>rotate(deg)</code>, <code>scale(sx, sy)</code>, <code>clip(x, y, w, h, r = 0)</code>.</p>
<h3>Цвета и градиенты</h3>
<table>
<tr><td class=k>"#ff3d81"  "#ff3d8180"  "red"</td><td>цвет строкой (#rrggbb, #rrggbbaa, имя)</td></tr>
<tr><td class=k>rgb(r, g, b, a = 1)</td><td>0…255 и прозрачность 0…1</td></tr>
<tr><td class=k>hsv(h, s = 1, v = 1, a = 1)</td><td>оттенок в градусах 0…360</td></tr>
<tr><td class=k>mix(c1, c2, t)   with_alpha(c, a)   hex(c)</td><td>смешать / прозрачность / в строку</td></tr>
<tr><td class=k>linear(x1, y1, x2, y2, stops)</td><td>линейный градиент</td></tr>
<tr><td class=k>radial(cx, cy, r, stops)   conic(cx, cy, angle, stops)</td><td>радиальный / конический</td></tr>
</table>
<p class=m>stops — список цветов (равномерно) или пар [[0, "#000"], [0.7, "#f0f"], [1, "#fff"]].
Градиент передаётся в fill() или stroke().</p>
"""),

("Математика", """
<h2>Математика и утилиты</h2>
<table>
<tr><td class=k>PI TAU E</td><td>константы</td></tr>
<tr><td class=k>sin cos tan asin acos atan atan2 sqrt abs min max floor ceil round pow exp ln log10 sign hypot deg rad</td><td>как обычно (радианы)</td></tr>
<tr><td class=k>clamp(v, lo = 0, hi = 1)  lerp(a, b, t)  remap(v, a, b, c, d)  smooth(t)</td><td>ограничить / смешать / перевести диапазон / плавный шаг</td></tr>
<tr><td class=k>noise(x, y = 0, z = 0)</td><td>плавный шум Перлина (-1…1) — для «живых» движений</td></tr>
<tr><td class=k>rand(a = 0, b = 1)  randint(a, b)  choice(list)  seed(s)</td><td>случайные числа</td></tr>
<tr><td class=k>len str num int</td><td>длина, в строку, в число</td></tr>
<tr><td class=k>push(list, v…) pop(list, i = -1) insert remove slice range sort reverse contains keys values</td><td>списки и словари</td></tr>
<tr><td class=k>sum(list, a, b)  avg(list, a, b)  fill_list(n, v)</td><td>сумма / среднее (части списка) / список из n одинаковых</td></tr>
<tr><td class=k>join split upper lower fmt(v, digits) fmt_time(sec)</td><td>строки; fmt_time(75) → "1:15"</td></tr>
<tr><td class=k>log(…)  print(…)</td><td>вывод в журнал студии</td></tr>
</table>
<h2>Анимация и физика</h2>
<table>
<tr><td class=k>approach(cur, target, rate, dt)</td><td>плавное приближение к цели, не зависит от FPS</td></tr>
<tr><td class=k>spring(pos, vel, target, k = 120, damping = 12, dt)</td><td>пружина → [новая позиция, новая скорость]</td></tr>
</table>
<pre>widget Bouncer {
  state y = 0
  state vy = 0
  on beat(p) { vy -= 400 * p }            // удар подбрасывает
  on frame(dt) {
    let s = spring(y, vy, 0, 90, 6, dt)
    y = s[0]
    vy = s[1]
  }
  on draw { fill(theme.accent); circle(w / 2, h / 2 + y, 18) }
}</pre>
"""),

("Скорость", """
<h2>Чтобы было плавно (60 кадров/с)</h2>
<p>Сцена рисуется <b>видеокартой</b> (OpenGL, сглаживание 4×), если она есть. Если нет, сцена рисуется
процессором. Переключатель — «Файл ▾ → Рисовать видеокартой». Скорость видна внизу студии:
кадров в секунду и время кадра.</p>
<ul>
<li>Статичное (фон, рамки, подписи) — <code>cache: true</code>; меняется редко — <code>cache_key</code>.</li>
<li>Тяжёлый светящийся визуализатор — <code>resolution: 0.5…0.7</code>. Это работает только при рисовании
процессором; на видеокарте виджет и так рисуется в полном разрешении.</li>
<li>Много отрезков — один <code>lines([...])</code> вместо <code>line()</code> в цикле.</li>
<li>Прозрачность — в цвете (<code>with_alpha(c, 0.5)</code>), а не <code>alpha()</code> на каждую фигуру.</li>
<li>Тяжёлые вычисления — в <code>on frame</code>, а в <code>on draw</code> — только рисование.</li>
<li>У каждого обработчика есть лимит шагов: бесконечный цикл не повесит плеер — виджет просто
остановится с ошибкой, а остальные продолжат работать.</li>
</ul>
<h2>Ошибки</h2>
<p>Синтаксическая ошибка — продолжает работать прежняя версия сцены, а внизу студии видно
строку и причину. Ошибка во время работы выключает только тот виджет, где она случилась.</p>
"""),

("Примеры", """
<h2>Примеры</h2>
<h3>Круговой спектр</h3>
<pre>widget Halo {
  state spin = 0
  on frame(dt) { spin += dt * (10 + audio.mid * 120) }
  on draw {
    let cx = w / 2
    let cy = h / 2
    let r0 = min(w, h) * 0.25
    for i in 0..64 {
      let a = rad(i * 360 / 64 + spin)
      let v = audio.fft[i]
      stroke(hsv(200 + v * 140, 0.8, 1), 3)
      line(cx + cos(a) * r0, cy + sin(a) * r0,
           cx + cos(a) * (r0 + 10 + v * r0), cy + sin(a) * (r0 + 10 + v * r0))
    }
  }
}</pre>
<h3>Кнопка</h3>
<pre>widget PlayButton {
  on click { toggle() }
  on draw {
    fill(hover ? theme.accent2 : theme.accent)
    circle(w / 2, h / 2, min(w, h) / 2)
    fill(theme.bg)
    text(player.playing ? "❚❚" : "▶", w / 2, h / 2, 18, align: "center")
  }
}</pre>
<h3>Частицы на бит</h3>
<pre>widget Sparks {
  state parts = []
  on beat(p) {
    for i in 0..floor(10 + p * 30) {
      let a = rand(0, TAU)
      let s = rand(60, 260) * p
      push(parts, {x: w / 2, y: h / 2, vx: cos(a) * s, vy: sin(a) * s, life: 1})
    }
  }
  on frame(dt) {
    let keep = []
    for q in parts {
      q.vy += 180 * dt                 // гравитация
      q.x += q.vx * dt
      q.y += q.vy * dt
      q.life -= dt
      if q.life > 0 { push(keep, q) }
    }
    parts = keep
  }
  on draw {
    blend("add")
    for q in parts { glow(q.x, q.y, 10, with_alpha(theme.accent, q.life)) }
    blend("normal")
  }
}</pre>
<h3>Перетаскиваемый ползунок громкости</h3>
<pre>widget Volume {
  on press(x, y) { set_volume(clamp(x / w)) }
  on drag(x, y) { set_volume(clamp(x / w)) }
  on wheel(d) { set_volume(clamp(player.volume + d * 0.05)) }
  on draw {
    fill("#ffffff22"); rect(0, h / 2 - 3, w, 6, 3)
    fill(theme.accent); rect(0, h / 2 - 3, w * player.volume, 6, 3)
    circle(w * player.volume, h / 2, 8)
  }
}</pre>
<p>Целая тема на EchoScript — встроенный скрипт <b>Loom</b> (откройте его в списке слева).</p>
"""),
]


def html() -> str:
    return CSS + "".join(body for _, body in SECTIONS)


def toc() -> list[str]:
    return [title for title, _ in SECTIONS]
