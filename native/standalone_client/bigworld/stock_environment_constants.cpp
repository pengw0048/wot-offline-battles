#include "stock_environment_constants.hpp"
#include "pch.hpp"

BW_BEGIN_NAMESPACE

void loadStockFogSettings(DataSectionPtr environment,
                          wot::StockFogSettings &settings) {
  if (!environment)
    return;
  DataSectionPtr fog = environment->openSection("Fog");
  if (!fog)
    return;
  // 0x00a55bd0 reads this historical spelling but discards the result.
  fog->readBool("enable", true);
  const char *scalars[] = {
      "nearLow",        "nearHigh",       "altitudeLow",     "altitudeMid",
      "farLow",         "farHigh",        "altitudeMid",     "altitudeHigh",
      "skyAltitudeLow", "skyAltitudeMid", "skyAltitudeHigh", "exponent"};
  auto vector = [&](const BW::string &name, wot::StockFogSettings::Group &group,
                    unsigned offset) {
    Vector4 initial(group[offset], group[offset + 1], group[offset + 2],
                    group[offset + 3]);
    Vector4 value = fog->readVector4(name, initial);
    for (unsigned i = 0; i < 4; ++i)
      group[offset + i] = value[i];
  };
  for (unsigned mode = 0; mode < 2; ++mode) {
    auto &group = mode ? settings.forward : settings.deferred;
    const BW::string prefix = mode ? "forward/" : "deferred/";
    const unsigned scalar_count = mode ? 11 : 12;
    for (unsigned i = 0; i < scalar_count; ++i)
      group[i] = fog->readFloat(prefix + scalars[i], group[i]);
    vector("outerBB", group, 12);
    vector("innerBB", group, 16);
    vector(prefix + "colorLow", group, 20);
    vector(prefix + "colorHigh", group, 24);
    if (!mode) {
      vector(prefix + "colorSunLow", group, 28);
      vector(prefix + "colorSunHigh", group, 32);
      group[38] = fog->readFloat(prefix + "sunAngle", group[38]);
      group[39] = fog->readFloat(prefix + "sunExponent", group[39]);
    }
    // The actual reader uses enabled, rather than the discarded enable.
    group[36] = fog->readBool("enabled", group[36] != 0.0f) ? 1.0f : 0.0f;
    group[37] =
        fog->readBool(prefix + "heightFog", group[37] != 0.0f) ? 1.0f : 0.0f;
    group[40] =
        fog->readBool(prefix + "useEdgeFog", group[40] != 0.0f) ? 1.0f : 0.0f;
  }
}

BW_END_NAMESPACE
