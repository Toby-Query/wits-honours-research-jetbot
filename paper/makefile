file=main

all:
	pdflatex $(file).tex
	bibtex $(file)
	pdflatex $(file).tex
	pdflatex $(file).tex

clean:
	rm -f *.aux *.bbl *.blg *.log *.out
