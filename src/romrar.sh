#!/bin/zsh

# Script to find and extract all RAR files in subdirectories
# Usage: ./romrar.sh

set -e  # Exit on error

echo "Searching for RAR files in subdirectories..."

# Counter for processed files
rar_count=0

# Find all .rar files in subdirectories and extract them
for rar_file in */*.rar **/*.rar(N); do
    if [[ -f "$rar_file" ]]; then
        dir="${rar_file%/*}"
        echo "Extracting: $rar_file"

        # Extract RAR file in its directory
        if unrar x -o+ "$rar_file" "$dir/"; then
            echo "  OK: Successfully extracted"
            ((rar_count++))
        else
            echo "  ERROR: Failed to extract"
        fi
    fi
done

echo ""
echo "Complete! Processed $rar_count RAR file(s)."