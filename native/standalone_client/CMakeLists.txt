cmake_minimum_required(VERSION 3.16)
project(wot_standalone_core LANGUAGES CXX)

add_library(wot_battle_core STATIC
    src/ballistics.cpp
    src/compiled_space.cpp
    src/point_velocity.cpp
)
target_include_directories(wot_battle_core PUBLIC include)
target_compile_features(wot_battle_core PUBLIC cxx_std_17)
set_target_properties(wot_battle_core PROPERTIES CXX_EXTENSIONS OFF)
if(MSVC)
    target_compile_options(wot_battle_core PRIVATE /W4 /WX /fp:strict)
else()
    target_compile_options(wot_battle_core PRIVATE
        -Wall -Wextra -Werror -pedantic -fno-fast-math -ffp-contract=off)
    if(CMAKE_SIZEOF_VOID_P EQUAL 4 AND CMAKE_SYSTEM_PROCESSOR MATCHES "i.86|x86")
        target_compile_options(wot_battle_core PRIVATE -msse2 -mfpmath=sse)
    endif()
endif()

add_executable(wot-native-core src/main.cpp)
target_link_libraries(wot-native-core PRIVATE wot_battle_core)

include(CTest)
if(BUILD_TESTING)
    add_executable(test_compiled_space tests/test_compiled_space.cpp)
    target_link_libraries(test_compiled_space PRIVATE wot_battle_core)
    add_test(NAME compiled_space COMMAND test_compiled_space)
    add_executable(test_point_velocity tests/test_point_velocity.cpp)
    target_link_libraries(test_point_velocity PRIVATE wot_battle_core)
    add_test(NAME point_velocity COMMAND test_point_velocity)
    add_executable(test_ballistics tests/test_ballistics.cpp)
    target_link_libraries(test_ballistics PRIVATE wot_battle_core)
    add_test(NAME ballistics COMMAND test_ballistics)
endif()
