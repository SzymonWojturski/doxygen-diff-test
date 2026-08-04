# Box Collapse

Generates a random matrix of connected Unicode pipes using Wave Function Collapse.

## Build

```bash
gcc -std=c11 -Wall -Wextra -O2 -o box_collapse box_collapse.c -lm
```

## Usage

```bash
./box_collapse <size> <pipe_percentage> [seed]
```

Example for a `15 × 15` matrix with `10%` pipe density:

```bash
./box_collapse 15 10
```

```
       ╔╗  ╔╦╗ 
       ╚╩╗ ╚╬╝ 
 ╔╦╗╔═╦═╗╚══╝  
 ╚╬╝║╔╣ ║      
  ╠╗╚╣║ ║      
  ║║ ║╚═╝      
  ╠╩╦╣         
  ╚╗╠╣╔╦╗      
   ║║║║╚╝      
   ╚╩╩╝╔═╗     
   ╔╦╗ ║ ║     
 ╔╗╚╣║ ║ ║     
 ╚╬╦╩╝ ╚═╝     
  ╚╝           
```
