cmake_minimum_required(VERSION 3.16)

# --- esp-sr movemodel.py: принудительный UTF-8 ---------------------------
#
# При сборке esp-sr выполняет `python model/movemodel.py`, который печатает
# UTF-8 (символы рамки и названия wake word, в т.ч. CJK). При кодировке
# консоли Windows cp1251/cp1252 это падает с:
#     UnicodeEncodeError: 'charmap' codec can't encode characters ...
#
# set(ENV{PYTHONUTF8} ...) здесь НЕ помогает: ninja запускается из idf.py,
# а не из этого процесса cmake, и потому не наследует изменённое окружение.
# Поэтому патчим правило самого компонента, подставляя `cmake -E env`.
#
# Патч идемпотентен и самовосстанавливается, если компонент будет
# перекачан менеджером компонентов. На первом «чистом» запуске, когда
# managed_components ещё нет, патч применится на следующем проходе cmake,
# который менеджер компонентов инициирует сам.
if(WIN32)
    set(_esp_sr_cmake "${CMAKE_CURRENT_LIST_DIR}/managed_components/espressif__esp-sr/CMakeLists.txt")

    if(EXISTS "${_esp_sr_cmake}")
        file(READ "${_esp_sr_cmake}" _esp_sr_content)

        if(_esp_sr_content MATCHES "COMMAND python")
            string(REPLACE
                "COMMAND python"
                "COMMAND ${CMAKE_COMMAND} -E env PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python"
                _esp_sr_content "${_esp_sr_content}")

            file(WRITE "${_esp_sr_cmake}" "${_esp_sr_content}")
            message(STATUS "Patched esp-sr CMakeLists for UTF-8 python output")
        endif()
    else()
        message(STATUS "esp-sr not downloaded yet, UTF-8 patch deferred")
    endif()
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wake_word_test)
