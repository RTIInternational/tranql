/**
* Callback for handling autocompletion within the query editor.
* 
* Note: Since this function is so long, it's been moved into its own file.
* App.js imports this function and binds it to the App context. It will not
* function outside of the App context. If this ever becomes problematic,
* just the parsing logic can be moved into its own function.
*
* @param {object} cm - The CodeMirror object.
* @private
*/
export default function autoComplete () {
 // https://github.com/JedWatson/react-codemirror/issues/52
 var codeMirror = this._codemirror;

 // hint options for specific plugin & general show-hint
 // 'tables' is sql-hint specific
 // 'disableKeywords' is also sql-hint specific, and undocumented but referenced in sql-hint plugin
 // Other general hint config, like 'completeSingle' and 'completeOnSingleClick'
 // should be specified here and will be honored

 // Shallow copy it.
 const pos = Object.assign({}, codeMirror.getCursor());
 const untrimmedPos = codeMirror.getCursor();
 const textToCursorPositionUntrimmed = codeMirror.getRange({ line : 0, ch : 0 }, { line : pos.line, ch : pos.ch });
 const textToCursorPosition = textToCursorPositionUntrimmed.trimRight();
 const entireText = codeMirror.getValue();

 // const splitLines = textToCursorPosition.split(/\r\n|\r|\n/);
 // // Adjust the position after trimming to be on the correct line.
 // pos.line = splitLines.length - 1;
 // // Adjust the position after trimming to be on the correct char.
 // pos.ch = splitLines[splitLines.length-1].length;

 const setHint = function(options, noResultsTip) {
   if (typeof noResultsTip === 'undefined') noResultsTip = true;
   if (noResultsTip && options.length === 0) {
     options.push({
       text: String(''),
       displayText:'No valid results'
     });
   }
   const hintOptions = {
     // tables: tables,
     hint: function() {
       return {
         from: pos,
         to: untrimmedPos,
         list: options.map((option) => {
           // Process custom options - `replaceText`
           if (option.hasOwnProperty('replaceText')) {
             let replaceText = option.replaceText;
             let from = option.hasOwnProperty('from') ? option.from : pos;
             let to = option.hasOwnProperty('to') ? option.to : untrimmedPos;

             option.from = { line : from.line, ch : from.ch - replaceText.length };
             option.to = { line : to.line, ch : to.ch};

             if (replaceText.length > 0) {
               const trimmedLines = textToCursorPositionUntrimmed.trimRight().split(/\r\n|\r|\n/);
               const lastLine = trimmedLines[trimmedLines.length-1];
               option.from.line = trimmedLines.length - 1;
               option.from.ch = lastLine.length - replaceText.length;
             }


             delete option.replaceText;
           }

           return option;
         })
       };
     },
     disableKeywords: true,
     completeSingle: false,
     completeOnSingleClick: false
   };

   codeMirror.showHint(hintOptions);
   // codeMirror.state.completionActive.pick = () => {
   //   codeMirror.showHint({
   //     hint: function() {
   //       return {
   //         from: pos,
   //         to: pos,
   //         list: [{
   //           text: String(''),
   //           displayText: 'foobar',
   //           className: 'testing'
   //         }]
   //       };
   //     },
   //     disableKeywords: true,
   //     completeSingle: false,
   //   });
   // }
 }

 const setError = (resultText, status, errors, resultOptions) => {
   if (typeof resultOptions === "undefined") resultOptions = {};
   codeMirror.showHint({
     hint: function() {
       return {
         from: pos,
         to: pos,
         list: [{
           text: String(''),
           displayText: resultText,
           className: 'autocomplete-result-error',
           ...resultOptions,
         }]
       };
     },
     disableKeywords: true,
     completeSingle: false,
   });
   if (typeof status !== "undefined" && typeof errors !== "undefined") {
     codeMirror.state.completionActive.pick = () => {
       this._handleMessageDialog (status, errors);
     }
   }
 }

 const setLoading = function(loading) {
   if (loading) {
     // text property has to be String('') because when it is '' (falsey) it refuses to display it.
     codeMirror.showHint({
       hint: function() {
         return {
           from: pos,
           to: pos,
           list: [{
             text: String(''),
             displayText: 'Loading',
             className: 'loading-animation'
           }]
         };
       },
       disableKeywords: true,
       completeSingle: false,
     });
   }
   else {
     codeMirror.closeHint();
   }
 }

 /**
  * TODO:
  * could try to see if its possible to have two select menus for predicates that also show concepts from the predicates
  *    would look something like this image, when, for example, you pressed the right arrow or left clicked or something on a predicate:
  *        https://i.imgur.com/LBsdrcq.png
  * could somehow see if there's a way to have predicate suggestion work properly when there's a concept already following the predicate
  *    Ex: 'select foo-[what_can_I_put_here?]->baz'
  *    Would involve sending more of the query instead of cutting it off at cursor.
  *    Then would somehow have to backtrack and locate which token the cursor's position translates to.
  */

 this._autoCompleteController.abort();
 this._autoCompleteController = new window.AbortController();

 setLoading(true);

 fetch(this.tranqlURL + '/tranql/parse_incomplete', {
   signal: this._autoCompleteController.signal,
   method: "POST",
   headers: {
     'Accept': 'application/json',
     'Content-Type': 'application/json',
   },
   body: JSON.stringify([textToCursorPositionUntrimmed, entireText])
 }).then(res => res.json())
   .then(async (parsedTree) => {
     setLoading(false)

     if (parsedTree.errors) {
       // this._handleMessageDialog (parsedTree.status, parsedTree.errors);
       setError("Failed to parse", parsedTree.status, parsedTree.errors);
     }
     else {
       setLoading(true);
       await this.schemaPromise;
       setLoading(false);
       const graph = this.state.schemaMessage.knowledge_graph;

       // Recursviely removes any tokens that are linebreaks from a parsed tree.
       const stripLinebreaks = function(tree) {
         if (Array.isArray(tree)) {
           return tree.filter((token) => stripLinebreaks(token));
         }
         else {
           return tree.toString().match(/\r\n|\r|\n/) === null;
         }
       }

       const incompleteTree = parsedTree[0];
       const completeTree = parsedTree[1];

       // Filter whitespace from the statements
       const block = incompleteTree[incompleteTree.length-1].map((statement) => {
         return stripLinebreaks(statement);
       });
       const completeBlock = completeTree[completeTree.length-1].map((statement) => {
         return stripLinebreaks(statement);
       });

       const lastStatement = block[block.length-1];
       const lastStatementComplete = completeBlock[block.length-1];

       const statementType = lastStatement[0];

       setLoading(true);
       const fromOptions = await this.reasonerURLs;
       setLoading(false);

       fromOptions["/schema"] = "/schema";

       const whereOptions = [
         'testing',
         'foobar'
       ];

       const concept_arrows = [
         '->',
         '<-'
       ];

       const all_arrows = [
         '->',
         '<-',
         '-[',
         '<-['
       ];

       const arrow_to_pred_arrow = (arrow) => {
         return {
           '->' : [
             '-[',
             '',
             ']->'
           ],
           '<-' : [
             '<-[',
             '',
             ']-'
           ]
         }[arrow];
       }

       const arrowToEmptyPredicate = (arrow) => {
         return arrow_to_pred_arrow(arrow);
       }

       const isBackwardsPredicate = (predicate) => {
         return predicate[0] === '<-[';
       }

       const toForwardPredicate = (predicate) => {
         predicate[0] = '-[';
         predicate[2] = ']->';
         return predicate;
       }

       const completePredicate = (predicate) => {
         if (isBackwardsPredicate (predicate)) {
           predicate[2] = arrow_to_pred_arrow("<-")[2];
         }
         else {
           predicate[2] = arrow_to_pred_arrow("->")[2];
         }
         return predicate;
       }

       const concept = (old_concept) => {
         // Concept identifiers aren't actually parsed by the lexer, but rather the ast in Query::add.
         // This just copies the methods that the ast uses to parse concept identifiers.
         if (old_concept.indexOf(":") !== -1) {
           const split = old_concept.split(":");
           if (split.length - 1 > 1) {
             throw new Error(`Invalid concept identifier "${old_concept}"`);
           }
           const [name, type_name] = split;
           return type_name;
         }
         else {
           return old_concept;
         }
       }

       const lastToken = lastStatement[lastStatement.length-1];
       const secondLastToken = lastStatement[lastStatement.length-2];
       const thirdLastToken = lastStatement[lastStatement.length-3];

       console.log(statementType, lastStatement, lastToken);

       // Try/catch the entirety of the logic
       try {
       if (statementType === 'select') {
         let validConcepts;
         if (lastToken === "-") {
           // Arrow suggestion
           // "select foo-"
           validConcepts = all_arrows.map((arrow) => {
             return {
               displayText: arrow,
               text: arrow,
               replaceText: "-"
             };
           });
         }
         else if (Array.isArray(lastToken) && lastToken.length < 3) {
           // If the last token is an array and not length 3 then it is an incomplete predicate.
           // "select foo-[" or "select foo-[bar"
           let currentPredicate = completePredicate([
             lastToken[0],
             lastToken[1] !== undefined ? lastToken[1] : ""
           ]);
           let previousConcept = concept(secondLastToken);
           // May be undefined if there is no next concept
           let has_no_next_concept = (lastStatementComplete.length - lastStatement.length) == 0;
           let nextConcept = has_no_next_concept? undefined : concept(lastStatementComplete[lastStatement.length]) ;
           // See https://github.com/frostyfan109/tranql/issues/117 for why this approach doesn't work



           const backwards = isBackwardsPredicate (currentPredicate);

           console.log ([previousConcept, currentPredicate, nextConcept]);

           // Should replace this method with reduce

           const allEdges = graph.edges.filter((edge) => {
             if (backwards) {
               return edge.target_id === previousConcept &&
               (nextConcept === undefined || edge.source_id === nextConcept) &&
               edge.type.startsWith(currentPredicate[1]);
             }
             else {
               return (
                 edge.source_id === previousConcept &&
                 (nextConcept === undefined || edge.target_id === nextConcept) &&
                 edge.type.startsWith(currentPredicate[1])
               );
             }
           });
           const uniqueEdgeMap = {};
           allEdges.forEach((edge) => {
             if (!uniqueEdgeMap.hasOwnProperty(edge.type)) {
               uniqueEdgeMap[edge.type] = edge;
             }
           });
           const uniqueEdges = Object.values(uniqueEdgeMap);
           validConcepts = uniqueEdges.map((edge) => {
             const replaceText = currentPredicate[1];
             // const actualText = type + currentPredicate[2];
             const conceptHint = " (" + (backwards ? edge.source_id : edge.target_id) + ")";
             const actualText = edge.type;
             const displayText = edge.type + conceptHint;
             return {
               displayText: displayText,
               text: actualText,
               replaceText : replaceText
             };
           });
         }
         else {
           // Otherwise, we are handling autocompletion of a concept.
           let currentConcept = "";
           let predicate = null;
           let previousConcept = null;

           if (lastToken === statementType) {
             // "select"
           }
           else if (secondLastToken === statementType) {
             // "select foo"
             currentConcept = concept(lastToken);
           }
           else if (concept_arrows.includes(lastToken) || Array.isArray(lastToken)) {
             // "select foo->" or "select foo-[bar]->"
             predicate = lastToken;
             previousConcept = concept(secondLastToken);
           }
           else {
             previousConcept = concept(thirdLastToken);
             predicate = secondLastToken;
             currentConcept = concept(lastToken);
           }


           if (predicate === null) {
             // Predicate will only be null if there are no arrows, and therefore the previousConcept is also null.
             // Single concept - just "select" or "select foo" where the concept is either "" or "foo"
             validConcepts = graph.nodes.filter((node) => node.type.startsWith(currentConcept)).map(node => node.type);
           }
           else {
             // If there is a predicate, we have to factor in the previous concept, the predicate, and the current concept.
             if (!Array.isArray(predicate)) {
               // We want to assign an empty predicate
               predicate = arrowToEmptyPredicate (predicate);
             }

             const backwards = isBackwardsPredicate (predicate);

             console.log ([previousConcept, predicate, currentConcept]);
             // Concepts could be named like select f1:foo->f2:bar
             // we need to split them and grab the actual types
             let previousConceptSplit = previousConcept.split(':');
             let currentConceptSplit = currentConcept.split(':');
             previousConcept = previousConceptSplit[previousConceptSplit.length - 1];
             currentConcept = currentConceptSplit[currentConceptSplit.length - 1];
             validConcepts = graph.edges.filter((edge) => {
               if (backwards) {
                 return (
                   edge.source_id.startsWith(currentConcept) &&
                   edge.target_id === previousConcept &&
                   (predicate[1] === "" || edge.type === predicate[1])
                 );
               }
               else {
                 return (
                   edge.source_id === previousConcept &&
                   edge.target_id.startsWith(currentConcept) &&
                   (predicate[1] === "" || edge.type === predicate[1])
                 );
               }
             }).map((edge) => {
               if (backwards) {
                 return edge.source_id;
               }
               else {
                 return edge.target_id
               }
             })
           }
           validConcepts = validConcepts.unique().map((concept) => {
             return {
               displayText: concept,
               text: concept,
               replaceText: currentConcept
             };
           });
         }
         setHint(validConcepts);

       }
       else if (statementType === 'from') {
         let currentReasonerArray = lastStatement[1];
         let startingQuote = "";
         if (currentReasonerArray === undefined) {
           // Adds an apostrophe to the start of the string if it doesn't have one ("from")
           startingQuote = "'";
           currentReasonerArray = [[
             "'",
             ""
           ]];
         }
         const endingQuote = currentReasonerArray[currentReasonerArray.length - 1][0];
         const currentReasoner = currentReasonerArray[currentReasonerArray.length - 1][1];
         // The select statement must be the first statement in the block, but thorough just in case.
         // We also want to filter out whitespace that would be detected as a token.
         const selectStatement = block.filter((statement) => statement[0] === "select")[0].filter((token) => {
           return typeof token !== "string" || token.match(/\s/) === null;
         });
         // Don't want the first token ("select")
         const tokens = selectStatement.slice(1);

         let validReasoners = [];

         Object.keys(fromOptions).forEach((reasoner) => {
           let valid = true;
           if (tokens.length === 1) {
             // Handles if there's only one concept ("select foo")
             const currentConcept = concept(tokens[0]);
             graph.nodes.filter((node) => node.type.startsWith(currentConcept)).forEach(node => node.reasoner.forEach((reasoner) => {
               !validReasoners.includes(reasoner) && validReasoners.push(reasoner);
             }));
           }
           else {
             for (let i=0;i<tokens.length-2;i+=2) {
               const previousConcept = concept(tokens[i]);
               let predicate = tokens[i+1];
               const currentConcept = concept(tokens[i+2]);

               if (!Array.isArray(predicate)) {
                 predicate = arrowToEmptyPredicate (predicate);
               }
               const backwards = isBackwardsPredicate (predicate);

               const isTransitionValid = graph.edges.filter((edge) => {
                 if (backwards) {
                   return (
                     edge.source_id.startsWith(currentConcept) &&
                     edge.target_id === previousConcept &&
                     (predicate[1] === "" || edge.type === predicate[1]) &&
                     (reasoner === "/schema" || edge.reasoner.includes(reasoner))
                   );
                 }
                 else {
                   return (
                     edge.source_id === previousConcept &&
                     edge.target_id.startsWith(currentConcept) &&
                     (predicate[1] === "" || edge.type === predicate[1]) &&
                     (reasoner === "/schema" || edge.reasoner.includes(reasoner))
                   );
                 }
               }).length > 0;
               if (!isTransitionValid) {
                 valid = false;
                 break;
               }
             }
             if (valid) {
               validReasoners.push(reasoner);
             }
           }
         });

         const validReasonerValues = validReasoners.map((reasoner) => {
           return fromOptions[reasoner];
         }).filter((reasonerValue) => {
           return reasonerValue.startsWith(currentReasoner);
         }).map((reasonerValue) => {
           return {
             displayText: reasonerValue,
             text: startingQuote + reasonerValue,
             // text: startingQuote + reasonerValue + endingQuote,
             replaceText: currentReasoner
           };
         });

         setHint(validReasonerValues);
       }
       else if (statementType === 'where') {

       }
       }
       catch (e) {
         setError('Failed to parse', 'Failed to parse', [{message: e.message, details: e.stack}]);
       }
     }
   })
   .catch((error) => {
     if (error.name !== "AbortError") {
       setError('Error', 'Error', [{message: error.message, details: error.stack}]);
     }
   });
}