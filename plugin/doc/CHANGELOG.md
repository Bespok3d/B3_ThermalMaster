# Changelog

## 0.28.0

- **A thermal timelapse of every print.** Switch it on in the new Timelapse panel of the settings
  page, and the plugin takes one frame each time the layer changes and makes a clip once the print
  ends. The clips are listed at the bottom of the settings page, to play, download or delete. It
  needs the slicer to send layer numbers; the documentation has the two lines to add.
- **The colours are chosen once for the whole clip**: fixed once the print has started, the whole
  print's coldest to hottest, two temperatures of your own, or the same as the live picture.
- **It keeps the last 10 prints**, or the number you choose, and makes room sooner if the
  printer's disk runs short.
- **Nothing gathered is lost.** Cancelled prints, prints cut short by a power cut, and layers the
  camera missed all end up in the clip.
- **Moonraker logins are handled.** If Moonraker asks for a login, the panel says so and takes
  Moonraker's API key, which is never shown again.

## 0.27.2

- **The documentation has pictures.** A picture of the viewer and three scenes at the top, the
  camera in Fluidd, the settings page, and one captured frame in each of the six palettes.
- **A section on the palettes**, and a table of every setting on the settings page with the JSON
  field that changes it at `/thermal/settings`, and an example of changing one.
- **It has run on a second printer**, a Raspberry Pi 4 Klipper printer, started by hand. The hardware
  status section says how that went and what it cost.
- Nothing about the plugin itself changes.

## 0.27.1

- **The plugin's manifest carries its publication dates**, when it was first published and when it
  was last updated, which 0.27.0 left out. Nothing about the plugin itself changes.

## 0.27.0

The first public release, signed by Bespok3d and published as a release candidate: it has run
daily on its maintainer's printer, with every version tested on the hardware, and moves to `stable`
once somebody else has run it too.

- **A thermal camera in Fluidd and Mainsail.** Plug in a Thermal Master P1 or P3 and it appears
  alongside your other cameras, under the name you choose when you install it. The plugin works
  out which model it is.
- **Temperatures in the picture.** A colour bar labelled with the range on screen, the temperature
  at the centre, and markers on the hottest and coldest points, in Celsius or Fahrenheit. Emissivity
  is applied to the readings, the gain switches between high sensitivity and a wide 0 to 550 C
  range, and the sensor can be recalibrated from the settings page.
- **An interactive viewer** at `/thermal/view`: point at the picture to read the temperature there,
  drag a box to measure a region, place up to four spots, zoom and pan, save a still or record a
  clip.
- **A display range you can hold**, so a colour means the same temperature in every frame.
- **Light on the printer.** It stops rendering when nobody is watching, can be switched off
  entirely, and its settings page says what it is costing the printer's processor.
- **It notices a dead picture.** If the connection drops, the viewer says what is wrong in a line
  across the picture, and reconnects by itself when it can.

The P1 is what this has been developed and run against. The P3 is implemented from the same driver
and has not been run on a printer yet: if you have one, a report either way is welcome.

The history before this release, 0.1.0 to 0.26.1, is in the repository as
[`CHANGELOG_DEV.md`](https://github.com/Bespok3d/B3_ThermalMaster/blob/main/CHANGELOG_DEV.md).
