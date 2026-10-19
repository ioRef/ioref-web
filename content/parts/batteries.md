---
title: Batteries
description: Batteries for powering projects
signal_type: n/a
image: 0892.jpg
related:
- relays
- transistor
- power-supplies
group: batteries
---

## What it is

A battery operating at 9V DC.

## When to use it

When you can power a portable project with 9V DC.

## How it works

9V (pronounced nine-volt) batteries are small packages containing smaller cells connected together. Standard 9V batteries contain six Alkaline battery cells connected in series. Rechargeable 9V batteries are also available.

**WARNING: _You should never attempt to tear open or take apart a 9V battery; the metal is very sharp and shorting the contacts on the battery may result in injury or fire._**

<figure class="image" style="text-align:center">   <img src="/images/parts/0892_inside1.jpg" alt="9V Battery Inside">   <figcaption style="text-align:center"><em>Image from author Lead holder via <a href="https://commons.wikimedia.org/wiki/File:9V_innards_3_different_cells.jpg">Wikimedia Commons</a></em></figcaption></figure>

## How to use it

9V batteries have a distinctive snap-on connector. To connect the clip, align the two circular connectors with the larger circle of the clip against the smaller circle of the battery.

You can use a 9V battery clip that has bare wires (image below) or one that goes to a barrel jack. The Arduino can be powered via a 9V battery by using the barrel jack or the `GND` and `Vin` pins. If using the pins, connect the black wire (ground) to `GND` and the red wire (9V) to `Vin`.

<figure class="image" style="text-align:center">   <img src="/images/parts/0892_clip1.jpg" alt="9V Battery Clip">   <figcaption style="text-align:center"><em>Image from author oomlout via <a href="https://commons.wikimedia.org/wiki/File:9_volt_Battery_Snap.jpg">Wikimedia Commons</a></em></figcaption></figure>

## Getting started

Since this is simply a battery, it will power any program you write to the Arduino.

<figure class="image" style="text-align:center">   <img src="/images/parts/0892_schematic_final.svg" alt="9V Battery Schematic">   <figcaption style="text-align:center"><em>For this example, the 9V battery uses the <code>Vin</code> pin.</em></figcaption></figure>
