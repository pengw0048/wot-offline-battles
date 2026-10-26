cmake_minimum_required(VERSION 3.16)
project(wot_standalone_core LANGUAGES CXX)

add_library(wot_battle_core STATIC
    src/ballistics.cpp
    src/combat_rules.cpp
    src/compiled_space.cpp
    src/point_velocity.cpp
    src/projectile_motion.cpp
    src/vehicle_dynamics.cpp
    src/vehicle_drive.cpp
    src/vehicle_control.cpp
    src/vehicle_collision.cpp
    src/gun_ballistics.cpp
    src/stock_gun_history.cpp
    src/stock_effect_binding.cpp
    src/stock_shader_binary.cpp
    src/stock_shader_runtime.cpp
    src/vehicle_physics.cpp
    src/vehicle_constraint_solver.cpp
    src/vehicle_suspension.cpp
    src/vehicle_authority.cpp
    src/wwise_bank.cpp
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

if(WOT_WWISE_SDK_ROOT)
    include(bigworld/wwise_sdk.cmake)
    add_executable(probe_wwise_sdk tests/probe_wwise_sdk.cpp
        "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Win32/AkDefaultIOHookBlocking.cpp"
        "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Common/AkFileLocationBase.cpp")
    target_link_libraries(probe_wwise_sdk PRIVATE wot_battle_core WotWwiseSdk)
endif()

include(CTest)
if(BUILD_TESTING)
    add_executable(test_stock_shader_runtime tests/test_stock_shader_runtime.cpp)
    target_link_libraries(test_stock_shader_runtime PRIVATE wot_battle_core)
    add_test(NAME stock_shader_runtime COMMAND test_stock_shader_runtime)
    add_executable(test_stock_effect_binding tests/test_stock_effect_binding.cpp)
    target_link_libraries(test_stock_effect_binding PRIVATE wot_battle_core)
    add_test(NAME stock_effect_binding COMMAND test_stock_effect_binding)
    add_executable(test_stock_shader_binary tests/test_stock_shader_binary.cpp)
    target_link_libraries(test_stock_shader_binary PRIVATE wot_battle_core)
    add_test(NAME stock_shader_binary COMMAND test_stock_shader_binary)
    add_executable(test_wwise_bank tests/test_wwise_bank.cpp)
    target_link_libraries(test_wwise_bank PRIVATE wot_battle_core)
    add_test(NAME wwise_bank COMMAND test_wwise_bank)
    add_executable(test_compiled_space tests/test_compiled_space.cpp)
    target_link_libraries(test_compiled_space PRIVATE wot_battle_core)
    add_test(NAME compiled_space COMMAND test_compiled_space)
    add_executable(test_point_velocity tests/test_point_velocity.cpp)
    target_link_libraries(test_point_velocity PRIVATE wot_battle_core)
    add_test(NAME point_velocity COMMAND test_point_velocity)
    add_executable(test_ballistics tests/test_ballistics.cpp)
    target_link_libraries(test_ballistics PRIVATE wot_battle_core)
    add_test(NAME ballistics COMMAND test_ballistics)
    add_executable(test_gun_ballistics tests/test_gun_ballistics.cpp)
    target_link_libraries(test_gun_ballistics PRIVATE wot_battle_core)
    add_test(NAME gun_ballistics COMMAND test_gun_ballistics)
    add_executable(test_stock_gun_history tests/test_stock_gun_history.cpp)
    target_link_libraries(test_stock_gun_history PRIVATE wot_battle_core)
    add_test(NAME stock_gun_history COMMAND test_stock_gun_history)
    add_executable(test_combat_rules tests/test_combat_rules.cpp)
    target_link_libraries(test_combat_rules PRIVATE wot_battle_core)
    add_test(NAME combat_rules COMMAND test_combat_rules)
    add_executable(test_projectile_motion tests/test_projectile_motion.cpp)
    target_link_libraries(test_projectile_motion PRIVATE wot_battle_core)
    add_test(NAME projectile_motion COMMAND test_projectile_motion)
    add_executable(test_vehicle_dynamics tests/test_vehicle_dynamics.cpp)
    target_link_libraries(test_vehicle_dynamics PRIVATE wot_battle_core)
    add_test(NAME vehicle_dynamics COMMAND test_vehicle_dynamics)
    add_executable(test_vehicle_constraint_solver tests/test_vehicle_constraint_solver.cpp)
    target_link_libraries(test_vehicle_constraint_solver PRIVATE wot_battle_core)
    add_test(NAME vehicle_constraint_solver COMMAND test_vehicle_constraint_solver)
    add_executable(test_vehicle_suspension tests/test_vehicle_suspension.cpp)
    target_link_libraries(test_vehicle_suspension PRIVATE wot_battle_core)
    add_test(NAME vehicle_suspension COMMAND test_vehicle_suspension)
    add_executable(test_vehicle_authority tests/test_vehicle_authority.cpp)
    target_link_libraries(test_vehicle_authority PRIVATE wot_battle_core)
    add_test(NAME vehicle_authority COMMAND test_vehicle_authority)
    add_executable(test_vehicle_drive tests/test_vehicle_drive.cpp)
    target_link_libraries(test_vehicle_drive PRIVATE wot_battle_core)
    add_test(NAME vehicle_drive COMMAND test_vehicle_drive)
    add_executable(test_vehicle_control tests/test_vehicle_control.cpp)
    target_link_libraries(test_vehicle_control PRIVATE wot_battle_core)
    add_test(NAME vehicle_control COMMAND test_vehicle_control)
    add_executable(test_vehicle_collision tests/test_vehicle_collision.cpp)
    target_link_libraries(test_vehicle_collision PRIVATE wot_battle_core)
    add_test(NAME vehicle_collision COMMAND test_vehicle_collision)
endif()
