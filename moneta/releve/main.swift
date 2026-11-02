// agent-smith-releve : lit le texte d'un relevé PDF avec PDFKit (macOS), sans service extérieur, sans rien écrire.
// Usage : agent-smith-releve <relevé.pdf>
// Répond une ligne JSON : {"pages": [{"numero": n, "largeur": l, "hauteur": h, "lignes": [{"x": x, "y": y, "texte": "..."}]}]}
// ou {"erreur": "..."}. Une « ligne » est un segment de texte : x et y en points depuis le coin en haut à gauche;
// de haut en bas, puis de gauche à droite.
// Aucune interprétation (ni montant ni date) : la lecture du relevé appartient au code Python.
import Foundation
import PDFKit

let maxPages = 50
let maxOutput = 2_000_000

func reply(_ object: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: object)
    FileHandle.standardOutput.write(data + Data("\n".utf8))
    exit(object["erreur"] == nil ? 0 : 1)
}

func point(_ value: CGFloat) -> Int {
    Int(value.rounded())
}

// Segments of text. Each character is located by a one-character selection of the document (the index of page.string,
// which counts the line breaks PDFKit adds; characterBounds(at:) does not). Rows by vertical centre, each from left to right.
// A space wider than 4 pt, or a gap wider than 3 pt, starts a new segment: the columns of a statement (date, description,
// withdrawal, deposit, balance) stay apart, each with its place.
let rowTolerance: CGFloat = 3.5

func segments(_ document: PDFDocument, _ page: PDFPage, _ box: CGRect) -> [[String: Any]] {
    guard let text = page.string else { return [] }
    let characters = text as NSString
    var glyphs: [(character: String, rect: CGRect)] = []
    for index in 0..<characters.length {
        let character = characters.substring(with: NSRange(location: index, length: 1))
        if character == "\n" || character == "\r" { continue }
        guard let one = document.selection(from: page, atCharacterIndex: index, to: page, atCharacterIndex: index) else { continue }
        let rect = one.bounds(for: page)
        if !rect.isEmpty { glyphs.append((character, rect)) }
    }
    glyphs.sort { $0.rect.midY > $1.rect.midY }
    var rows: [[(character: String, rect: CGRect)]] = []
    for glyph in glyphs {
        if let centre = rows.last?.first?.rect.midY, centre - glyph.rect.midY <= rowTolerance {
            rows[rows.count - 1].append(glyph)
        } else {
            rows.append([glyph])
        }
    }
    var out: [[String: Any]] = []
    for row in rows {
        let top = row.map { $0.rect.maxY }.max()!
        var current = "", start: CGFloat = 0, last = CGRect.null
        func close() {
            let value = current.trimmingCharacters(in: .whitespaces)
            if !value.isEmpty { out.append(["x": point(start - box.minX), "y": point(box.maxY - top), "texte": value]) }
            current = ""
        }
        for glyph in row.sorted(by: { $0.rect.minX < $1.rect.minX }) {
            let blank = glyph.character.trimmingCharacters(in: .whitespaces).isEmpty
            if blank {
                if glyph.rect.width > 4 { close() } else if !current.isEmpty { current += " " }
            } else {
                if current.isEmpty || glyph.rect.minX - last.maxX > 3 {
                    close()
                    start = glyph.rect.minX
                }
                current += glyph.character
            }
            last = glyph.rect
        }
        close()
    }
    return out
}

let arguments = CommandLine.arguments
guard arguments.count == 2 else { reply(["erreur": "usage : agent-smith-releve <relevé.pdf>"]) }
let url = URL(fileURLWithPath: arguments[1])
guard let document = PDFDocument(url: url) else { reply(["erreur": "PDF illisible"]) }
guard !document.isEncrypted, !document.isLocked else { reply(["erreur": "PDF chiffré ou protégé par mot de passe"]) }
guard document.pageCount > 0 else { reply(["erreur": "PDF sans page"]) }
guard document.pageCount <= maxPages else { reply(["erreur": "plus de \(maxPages) pages"]) }

var pages: [[String: Any]] = []
for index in 0..<document.pageCount {
    guard let page = document.page(at: index) else { reply(["erreur": "page \(index + 1) illisible"]) }
    let box = page.bounds(for: .mediaBox)
    let text = page.string ?? ""
    guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        reply(["erreur": "page \(index + 1) sans texte (relevé numérisé ?)"])
    }
    let lines = segments(document, page, box)
    pages.append(["numero": index + 1, "largeur": point(box.width), "hauteur": point(box.height), "lignes": lines])
}
let result: [String: Any] = ["pages": pages]
let size = (try? JSONSerialization.data(withJSONObject: result))?.count ?? Int.max
guard size <= maxOutput else { reply(["erreur": "relevé trop long (texte de plus de 2 Mo)"]) }
reply(result)
