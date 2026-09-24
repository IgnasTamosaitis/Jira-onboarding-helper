// Minimal Office Scripts declarations used to type-check ReserveAccessCard.ts.
declare namespace ExcelScript {
  interface Workbook {
    getWorksheet(name: string): Worksheet | undefined;
  }

  interface Worksheet {
    getRange(address: string): Range;
    getRangeByIndexes(startRow: number, startColumn: number, rowCount: number, columnCount: number): Range;
  }

  interface Range {
    getTexts(): string[][];
    getUsedRange(valuesOnly?: boolean): Range | undefined;
    setValues(values: (string | number | boolean)[][]): void;
  }
}
