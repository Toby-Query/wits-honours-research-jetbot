file=main
out=2596286_Research_Report

all:
	pdflatex -jobname=$(out) $(file).tex
	bibtex $(out)
	pdflatex -jobname=$(out) $(file).tex
	pdflatex -jobname=$(out) $(file).tex

clean:
	rm -f *.aux *.bbl *.blg *.log *.out
