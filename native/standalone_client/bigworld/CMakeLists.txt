# Included from the pinned BigWorld client/CMakeLists.txt after bwclient exists.
if(NOT TARGET bwclient)
    message(FATAL_ERROR "The standalone application must be linked into bwclient")
endif()

set(WOT_NUMERICAL_SOURCES
    "${CMAKE_CURRENT_LIST_DIR}/../src/projectile_motion.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_control.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/combat_rules.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_collision.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/gun_ballistics.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/stock_gun_history.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_effect_dx9.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_effect_adapter.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/stock_shader_binary.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/stock_effect_binding.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/stock_effect_reflection.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/stock_shader_runtime.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/point_velocity.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/wwise_bank.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_dynamics.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_drive.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_physics.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_suspension.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_authority.cpp")
set(WOT_STOCK_ADAPTER_SOURCES
    "${CMAKE_CURRENT_LIST_DIR}/stock_startup_services.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_particle_quality.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_environment_constants.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/client_session.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/gun_ballistics_binding.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/offline_vehicle_parameters.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/vehicle_appearance.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/vehicle_physics_binding.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_scene_collision.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_scene_identity.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_scene_prediction.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_avatar_filter_helper.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_audio.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_audio_io.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_vehicle_components.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_vehicle_animators.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_vehicle_audio.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_vehicle_fashions.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_vehicle_visuals.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/stock_vehicle_filter.cpp")
target_sources(bwclient PRIVATE "${CMAKE_CURRENT_LIST_DIR}/standalone_app.cpp"
    ${WOT_NUMERICAL_SOURCES} ${WOT_STOCK_ADAPTER_SOURCES})
target_include_directories(bwclient PRIVATE "${BW_SOURCE_DIR}/client"
    "${CMAKE_CURRENT_LIST_DIR}"
    "${CMAKE_CURRENT_LIST_DIR}/../include")
target_include_directories(moo PUBLIC "${CMAKE_CURRENT_LIST_DIR}"
    "${CMAKE_CURRENT_LIST_DIR}/../include")
target_include_directories(romp PUBLIC "${CMAKE_CURRENT_LIST_DIR}"
    "${CMAKE_CURRENT_LIST_DIR}/../include")
include("${CMAKE_CURRENT_LIST_DIR}/wwise_sdk.cmake")
target_link_libraries(bwclient WotWwiseSdk)
target_sources(bwclient PRIVATE
    "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Common/AkFilePackage.cpp"
    "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Common/AkFilePackageLUT.cpp"
    "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Common/AkFileLocationBase.cpp")
# These sources are appended after the legacy client's PCH source enumeration.
# Keep its C++14/PCH contract intact; the recovered numerical core uses C++17.
if(MSVC)
    set_source_files_properties(${WOT_NUMERICAL_SOURCES} ${WOT_STOCK_ADAPTER_SOURCES}
        PROPERTIES COMPILE_OPTIONS "/std:c++17;/fp:strict")
    # These adapters include the public engine's C++14 STL declarations.
    # Retain that observed surface in their otherwise C++17 translation units.
    set_source_files_properties(
        ${WOT_STOCK_ADAPTER_SOURCES}
        PROPERTIES COMPILE_DEFINITIONS
        "_HAS_AUTO_PTR_ETC=1;_HAS_TR1_NAMESPACE=1;_ENFORCE_MATCHING_ALLOCATORS=0")
    # The bundled Python 2.7 declarations still use register parameters.
    # Keep that legacy-header warning local to the engine adapters.
    set_property(SOURCE
        ${WOT_STOCK_ADAPTER_SOURCES}
        APPEND PROPERTY COMPILE_OPTIONS "/wd5033")
else()
    set_source_files_properties(${WOT_NUMERICAL_SOURCES} ${WOT_STOCK_ADAPTER_SOURCES}
        PROPERTIES COMPILE_OPTIONS "-std=c++17;-fno-fast-math;-ffp-contract=off")
endif()
